# -*- mode: python -*-
import errno
import io
import json
import os
import stat

import httpx
import pytest
import respx

from nbank import archive, check, transfer, util
from test.test_registry import base_url, bulk_url

bulk_locations_url = bulk_url + "locations/"


@pytest.fixture
def tmp_archive(tmp_path):
    return archive.create(tmp_path / "archive", base_url, allow_directories=True)


def store(cfg, tmp_path, name, contents="contents"):
    src = tmp_path / name
    src.write_text(contents)
    sha1 = util.hash(src)
    return archive.store_resource(cfg, src, id=name), sha1


def location(archive_name, root, name, scheme="neurobank"):
    return {
        "archive_name": archive_name,
        "scheme": scheme,
        "root": str(root),
        "resource_name": name,
    }


def record(name, sha1, *locations):
    return {"name": name, "sha1": sha1, "filename": name, "locations": list(locations)}


class FakeBulkLocations:
    """Answers bulk location requests from a dict of records, like the registry."""

    def __init__(self, respx_mock, records):
        self.records = {r["name"]: r for r in records}
        self.requests = []
        respx_mock.post(bulk_locations_url).mock(side_effect=self.respond)

    def respond(self, request):
        query = json.loads(request.content)
        self.requests.append(query)
        archive_name = query.get("archive")
        lines = []
        for name in query["names"]:
            rec = self.records.get(name)
            if rec is None:
                continue
            locs = [
                loc
                for loc in rec["locations"]
                if archive_name is None or loc["archive_name"] == archive_name
            ]
            if locs:
                lines.append(json.dumps({**rec, "locations": locs}) + "\n")
        return httpx.Response(200, content="".join(lines).encode())


def find(ids, **kwargs):
    with httpx.Client() as session:
        return list(transfer.find_sources(session, base_url, ids, **kwargs))


@respx.mock(assert_all_mocked=True)
def test_find_local_copy(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [record("res_1", sha1, location("arch", tmp_archive["path"], "res_1"))],
    )
    [source] = find(["res_1"])
    assert source == transfer.Source(
        "res_1", sha1=sha1, filename="res_1", path=path, archive="arch"
    )
    assert source.ok


@respx.mock(assert_all_mocked=True)
def test_find_skips_locations_not_on_this_host(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [
            record(
                "res_1",
                sha1,
                location("tape", "tape01:1", "res_1", scheme="tape"),
                location("elsewhere", tmp_path / "not-mounted", "res_1"),
                location("arch", tmp_archive["path"], "res_1"),
            )
        ],
    )
    [source] = find(["res_1"])
    assert (source.path, source.archive) == (path, "arch")


@respx.mock(assert_all_mocked=True)
def test_find_skips_download_location(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [
            record(
                "res_1",
                sha1,
                location(
                    "registry", "localhost:8000/neurobank/download", "res_1", "https"
                ),
                location("arch", tmp_archive["path"], "res_1"),
            )
        ],
    )
    [source] = find(["res_1"])
    assert (source.path, source.archive) == (path, "arch")


@respx.mock(assert_all_mocked=True)
def test_find_no_copy_on_this_host(respx_mock, tmp_path):
    FakeBulkLocations(
        respx_mock,
        [record("res_1", None, location("elsewhere", tmp_path / "gone", "res_1"))],
    )
    [source] = find(["res_1"])
    assert not source.ok
    assert source.error == "no copy on this host"


@respx.mock(assert_all_mocked=True)
def test_find_unregistered(respx_mock):
    FakeBulkLocations(respx_mock, [])
    [source] = find(["missing"])
    assert source == transfer.Source(
        "missing", error="not in the registry, or has no locations"
    )


@respx.mock(assert_all_mocked=True)
def test_find_in_archive(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    _, other_sha1 = store(tmp_archive, tmp_path, "res_2")
    fake = FakeBulkLocations(
        respx_mock,
        [
            record("res_1", sha1, location("arch", tmp_archive["path"], "res_1")),
            record(
                "res_2", other_sha1, location("other", tmp_archive["path"], "res_2")
            ),
        ],
    )
    sources = find(["res_1", "res_2"], archive="arch")
    assert fake.requests[0]["archive"] == "arch"
    assert sources[0].path == path
    assert sources[1].error == "not in archive 'arch'"


@pytest.mark.skipif(os.getuid() == 0, reason="root can read anything")
@respx.mock(assert_all_mocked=True)
def test_find_unreadable(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [record("res_1", sha1, location("arch", tmp_archive["path"], "res_1"))],
    )
    path.chmod(0o000)
    try:
        [source] = find(["res_1"])
    finally:
        path.chmod(0o444)
    assert not source.ok
    assert source.error == f"'{path}' is not readable"


@pytest.mark.skipif(os.getuid() == 0, reason="root can read anything")
@respx.mock(assert_all_mocked=True)
def test_find_directory_with_unreadable_contents(respx_mock, tmp_archive, tmp_path):
    src = tmp_path / "res_1"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("contents")
    path = archive.store_resource(tmp_archive, src)
    FakeBulkLocations(
        respx_mock,
        [record("res_1", None, location("arch", tmp_archive["path"], "res_1"))],
    )
    inner = path / "sub" / "data"
    inner.chmod(0o000)
    try:
        [source] = find(["res_1"])
    finally:
        inner.chmod(0o444)
    assert source.error == f"'{inner}' is not readable"


@respx.mock(assert_all_mocked=True)
def test_find_keeps_order_and_drops_duplicates(
    respx_mock, tmp_archive, tmp_path, monkeypatch
):
    monkeypatch.setattr(util, "bulk_batch_size", 2)
    records = []
    for name in ("res_a", "res_b", "res_c"):
        _, sha1 = store(tmp_archive, tmp_path, name, contents=name)
        records.append(record(name, sha1, location("arch", tmp_archive["path"], name)))
    fake = FakeBulkLocations(respx_mock, records)
    sources = find(["res_c", "res_a", "res_c", "missing", "res_b"])
    assert [s.id for s in sources] == ["res_c", "res_a", "missing", "res_b"]
    assert [s.ok for s in sources] == [True, True, False, True]
    # four distinct ids, two per request
    assert [len(q["names"]) for q in fake.requests] == [2, 2]


def sha1_of(data: bytes) -> str:
    import hashlib

    return hashlib.sha1(data).hexdigest()


def leftovers(cfg):
    """Temporary files or directories left in the archive."""
    return [p for p in (cfg["path"] / "resources").rglob(".*.partial")]


def test_receive_file(tmp_archive):
    data = b"the contents"
    received = transfer.receive_file(
        tmp_archive, "res_1", "original.wav", io.BytesIO(data), sha1_of(data)
    )
    target = tmp_archive["path"] / "resources" / "re" / "res_1.wav"
    assert received == transfer.Received(target, sha1_of(data), True)
    assert target.read_bytes() == data
    assert stat.S_IMODE(target.stat().st_mode) & 0o222 == 0
    assert leftovers(tmp_archive) == []
    assert list(check.check_archive_permissions(tmp_archive)) == []


def test_receive_file_without_extension(tmp_path):
    cfg = archive.create(tmp_path / "archive", base_url, keep_extensions=False)
    received = transfer.receive_file(
        cfg, "res_1", "original.wav", io.BytesIO(b"x"), None
    )
    assert received.path.name == "res_1"


def test_receive_file_without_registered_hash(tmp_archive):
    received = transfer.receive_file(tmp_archive, "res_1", "f", io.BytesIO(b"x"), None)
    assert received.path.exists()
    assert not received.verified


def test_receive_file_hash_mismatch(tmp_archive):
    with pytest.raises(transfer.TransferError, match="don't match"):
        transfer.receive_file(
            tmp_archive, "res_1", "f.wav", io.BytesIO(b"x"), sha1_of(b"other")
        )
    with pytest.raises(FileNotFoundError):
        archive.resource_path(tmp_archive, "res_1", resolve_ext=True)
    assert leftovers(tmp_archive) == []


def test_receive_file_upper_case_registered_hash(tmp_archive):
    received = transfer.receive_file(
        tmp_archive, "res_1", "f", io.BytesIO(b"x"), sha1_of(b"x").upper()
    )
    assert received.verified


def test_receive_file_already_in_archive(tmp_archive, tmp_path):
    existing, _ = store(tmp_archive, tmp_path, "res_1.json")
    with pytest.raises(transfer.TransferError, match="already in the archive"):
        transfer.receive_file(tmp_archive, "res_1", "f.wav", io.BytesIO(b"x"), None)
    assert existing.read_text() == "contents"
    assert leftovers(tmp_archive) == []


def test_receive_file_keeps_leftover_partial(tmp_archive):
    stub = tmp_archive["path"] / "resources" / "re"
    stub.mkdir()
    leftover = stub / transfer.partial_name("res_1.wav")
    leftover.write_text("from a crash")
    with pytest.raises(transfer.TransferError, match="left over"):
        transfer.receive_file(tmp_archive, "res_1", "f.wav", io.BytesIO(b"x"), None)
    assert leftover.read_text() == "from a crash"


def test_receive_file_cleans_up_after_read_error(tmp_archive):
    class Broken(io.RawIOBase):
        def readable(self):
            return True

        def readinto(self, buffer):
            raise OSError("tape read error")

    with pytest.raises(transfer.TransferError, match="tape read error"):
        transfer.receive_file(tmp_archive, "res_1", "f.wav", Broken(), None)
    assert leftovers(tmp_archive) == []


def test_receive_file_interrupted(tmp_archive):
    class Interrupted(io.RawIOBase):
        def readable(self):
            return True

        def readinto(self, buffer):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        transfer.receive_file(tmp_archive, "res_1", "f.wav", Interrupted(), None)
    assert leftovers(tmp_archive) == []


def test_receive_file_without_hard_links(tmp_archive, monkeypatch):
    def no_links(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    received = transfer.receive_file(tmp_archive, "res_1", "f", io.BytesIO(b"x"), None)
    assert received.path.read_bytes() == b"x"
    assert leftovers(tmp_archive) == []


@pytest.mark.parametrize("hard_links", [True, False])
def test_receive_file_never_replaces(tmp_archive, monkeypatch, hard_links):
    # another process stores the resource while this one is writing it
    target = tmp_archive["path"] / "resources" / "re" / "res_1.wav"

    class Racing(io.RawIOBase):
        done = False

        def readable(self):
            return True

        def readinto(self, buffer):
            if self.done:
                return 0
            target.write_text("stored by someone else")
            buffer[:1] = b"x"
            self.done = True
            return 1

    if not hard_links:

        def no_links(src, dst):
            raise OSError(errno.EPERM, "Operation not permitted")

        monkeypatch.setattr(os, "link", no_links)
    with pytest.raises(transfer.TransferError, match="appeared"):
        transfer.receive_file(tmp_archive, "res_1", "f.wav", Racing(), None)
    assert target.read_text() == "stored by someone else"
    assert leftovers(tmp_archive) == []


def test_receive_directory_never_replaces(tmp_archive):
    # another process stores the resource while this one is writing it
    target = tmp_archive["path"] / "resources" / "re" / "res_1"

    def racing_entries():
        yield ("a.txt", io.BytesIO(b"first"))
        target.mkdir()
        (target / "theirs").write_text("stored by someone else")
        yield ("b.txt", io.BytesIO(b"second"))

    with pytest.raises(transfer.TransferError, match="appeared"):
        transfer.receive_directory(
            tmp_archive, "res_1", "res_1", racing_entries(), None
        )
    assert [p.name for p in target.iterdir()] == ["theirs"]
    assert leftovers(tmp_archive) == []


def test_receive_flushes_to_disk(tmp_archive, monkeypatch):
    synced = []
    real_fsync = os.fsync

    def fsync(fd):
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    transfer.receive_file(tmp_archive, "res_1", "f", io.BytesIO(b"x"), None)
    # the file, then the directory entry for its new name
    assert len(synced) == 2
    synced.clear()
    transfer.receive_directory(tmp_archive, "res_2", "res_2", directory_entries(), None)
    assert len(synced) == len(directory_contents) + 1


def test_receive_file_dry_run(tmp_archive):
    data = b"the contents"
    received = transfer.receive_file(
        None, "res_1", "f.wav", io.BytesIO(data), sha1_of(data)
    )
    assert received == transfer.Received(None, sha1_of(data), True)
    assert not (tmp_archive["path"] / "resources" / "re").exists()
    with pytest.raises(transfer.TransferError, match="don't match"):
        transfer.receive_file(None, "res_1", "f.wav", io.BytesIO(data), sha1_of(b"x"))


directory_contents = {"a.txt": b"first", "sub/b.bin": b"second", "sub/deeper/c": b""}


def directory_entries(contents=directory_contents):
    entries = [("sub", None)]
    entries += [(rel, io.BytesIO(data)) for rel, data in contents.items()]
    return entries


def directory_hash(tmp_path, contents=directory_contents):
    root = tmp_path / "reference"
    for rel, data in contents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return util.hash_directory(root)


def test_receive_directory(tmp_archive, tmp_path):
    sha1 = directory_hash(tmp_path)
    entries = list(reversed(directory_entries()))
    received = transfer.receive_directory(tmp_archive, "res_1", "res_1", entries, sha1)
    target = tmp_archive["path"] / "resources" / "re" / "res_1"
    assert received == transfer.Received(target, sha1, True)
    assert util.hash_directory(target) == sha1
    assert (target / "sub" / "b.bin").read_bytes() == b"second"
    for path in (target, target / "sub", target / "sub" / "b.bin"):
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0
    assert leftovers(tmp_archive) == []
    assert list(check.check_archive_permissions(tmp_archive)) == []


def test_receive_directory_hash_mismatch(tmp_archive):
    with pytest.raises(transfer.TransferError, match="don't match"):
        transfer.receive_directory(
            tmp_archive, "res_1", "res_1", directory_entries(), sha1_of(b"x")
        )
    assert not (tmp_archive["path"] / "resources" / "re" / "res_1").exists()
    assert leftovers(tmp_archive) == []


@pytest.mark.parametrize(
    "relpath", ["../escape", "/etc/passwd", "sub/../../escape", "", "."]
)
def test_receive_directory_refuses_paths_outside(tmp_archive, tmp_path, relpath):
    entries = [*directory_entries(), (relpath, io.BytesIO(b"x"))]
    with pytest.raises(transfer.TransferError, match="not a path inside"):
        transfer.receive_directory(tmp_archive, "res_1", "res_1", entries, None)
    assert leftovers(tmp_archive) == []
    assert not (tmp_archive["path"] / "resources" / "escape").exists()
    with pytest.raises(transfer.TransferError, match="not a path inside"):
        transfer.receive_directory(None, "res_1", "res_1", entries, None)


def test_receive_directory_refuses_repeated_path(tmp_archive):
    entries = [*directory_entries(), ("a.txt", io.BytesIO(b"again"))]
    with pytest.raises(transfer.TransferError, match="more than once"):
        transfer.receive_directory(tmp_archive, "res_1", "res_1", entries, None)
    assert leftovers(tmp_archive) == []


def test_receive_directory_dry_run(tmp_archive, tmp_path):
    sha1 = directory_hash(tmp_path)
    received = transfer.receive_directory(
        None, "res_1", "res_1", directory_entries(), sha1
    )
    assert received == transfer.Received(None, sha1, True)
    assert not (tmp_archive["path"] / "resources" / "re").exists()


add_location_url = f"{base_url}resources/res_1/locations/"


def stored_file(cfg):
    return transfer.receive_file(cfg, "res_1", "f", io.BytesIO(b"x"), None).path


@respx.mock(assert_all_mocked=True)
def test_add_location(respx_mock, tmp_archive):
    path = stored_file(tmp_archive)
    route = respx_mock.post(add_location_url).respond(201, json={})
    with httpx.Client() as session:
        transfer.add_location(session, base_url, "res_1", "arch", path)
    assert json.loads(route.calls.last.request.content) == {"archive_name": "arch"}
    assert path.exists()


@respx.mock(assert_all_mocked=True)
def test_add_location_refused_removes_copy(respx_mock, tmp_archive):
    path = stored_file(tmp_archive)
    respx_mock.post(add_location_url).respond(
        400, json={"archive_name": ["no such archive 'arch'"]}
    )
    with (
        httpx.Client() as session,
        pytest.raises(transfer.TransferError, match="no such archive"),
    ):
        transfer.add_location(session, base_url, "res_1", "arch", path)
    assert not path.exists()


@respx.mock(assert_all_mocked=True)
def test_add_location_unreachable_keeps_copy(respx_mock, tmp_archive):
    path = stored_file(tmp_archive)
    respx_mock.post(add_location_url).mock(side_effect=httpx.ConnectError("refused"))
    with (
        httpx.Client() as session,
        pytest.raises(transfer.TransferError, match="nbank check archive"),
    ):
        transfer.add_location(session, base_url, "res_1", "arch", path)
    assert path.exists()


@pytest.mark.parametrize(
    "name, target",
    [
        (transfer.partial_name("res_1.wav"), "res_1.wav"),
        (transfer.partial_name("res_1"), "res_1"),
        ("res_1.partial", None),
        (".res_1.wav", None),
        (".partial", None),
        ("..partial", None),
    ],
)
def test_partial_target(name, target):
    assert transfer.partial_target(name) == target
