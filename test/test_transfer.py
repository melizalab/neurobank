# -*- mode: python -*-
import errno
import io
import json
import os
import stat
import tarfile
import zipfile

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


def tar_bytes(members):
    """A tar file of (name, contents) members; contents None is a directory, and
    'link' a symbolic link."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif data == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = "elsewhere"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def read_tar(members, registered, read=True):
    """Runs iter_tar_resources over members; returns what it yields, read."""
    tar = tarfile.open(fileobj=io.BytesIO(tar_bytes(members)), mode="r|")

    def lookup(id, member):
        return {"name": id} if id in registered else None

    results = []
    for res in transfer.iter_tar_resources(tar, lookup):
        if not read:
            results.append((res.record["name"], res.name, res.is_dir))
        elif res.is_dir:
            entries = [(rel, None if s is None else s.read()) for rel, s in res.data]
            results.append((res.record["name"], res.name, entries))
        else:
            results.append((res.record["name"], res.name, res.data.read()))
    return results


def test_tar_resources_files():
    members = [
        ("res_1.wav", b"one"),
        ("other.txt", b"not registered"),
        ("res_2", b"two"),
    ]
    assert read_tar(members, {"res_1", "res_2"}) == [
        ("res_1", "res_1.wav", b"one"),
        ("res_2", "res_2", b"two"),
    ]


def test_tar_resources_full_paths():
    # as written by `nbank locate -0 | xargs -0 tar -cf`
    members = [
        ("home/data/arch/resources/re/res_1.wav", b"one"),
        ("home/data/arch/resources/re/res_2", None),
        ("home/data/arch/resources/re/res_2/data", b"two"),
    ]
    assert read_tar(members, {"res_1", "res_2"}) == [
        ("res_1", "res_1.wav", b"one"),
        ("res_2", "res_2", [("data", b"two")]),
    ]


def test_tar_resources_directory():
    members = [
        ("unregistered_dir", None),
        ("res_d", None),
        ("res_d/sub", None),
        ("res_d/sub/f", b"inside"),
        ("res_d/g", b"top"),
        ("res_f.txt", b"after"),
    ]
    assert read_tar(members, {"res_d", "res_f"}) == [
        ("res_d", "res_d", [("sub", None), ("sub/f", b"inside"), ("g", b"top")]),
        ("res_f", "res_f.txt", b"after"),
    ]


def test_tar_resources_skips_unread_directory():
    members = [
        ("res_d", None),
        ("res_d/f", b"inside"),
        ("res_d/g", b"inside"),
        ("res_f.txt", b"after"),
    ]
    tar = tarfile.open(fileobj=io.BytesIO(tar_bytes(members)), mode="r|")
    items = transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
    res = next(items)
    assert (res.record["name"], res.is_dir, res.size) == ("res_d", True, None)
    # the directory's entries are never read
    res = next(items)
    assert (res.record["name"], res.name, res.size) == ("res_f", "res_f.txt", 5)
    assert res.data.read() == b"after"
    assert next(items, None) is None


def test_tar_resources_directory_with_link():
    members = [
        ("res_d", None),
        ("res_d/f", b"inside"),
        ("res_d/link", "link"),
        ("res_f.txt", b"after"),
    ]
    tar = tarfile.open(fileobj=io.BytesIO(tar_bytes(members)), mode="r|")
    items = transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
    entries = next(items).data
    with pytest.raises(transfer.TransferError, match="res_d/link"):
        list(entries)
    res = next(items)
    assert (res.record["name"], res.data.read()) == ("res_f", b"after")


def test_open_tar_from_stdin(monkeypatch):
    # a pipe can't seek, like a tape drive
    read_fd, write_fd = os.pipe()
    os.write(write_fd, tar_bytes([("res_1.wav", b"one")]))
    os.close(write_fd)

    class Stdin:
        buffer = open(read_fd, "rb")

    monkeypatch.setattr("sys.stdin", Stdin)
    with Stdin.buffer, transfer.open_tar("-") as tar:
        items = transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
        results = [(res.name, res.data.read()) for res in items]
    assert results == [("res_1.wav", b"one")]


@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_reports_progress(tmp_archive, monkeypatch, dry_run):
    monkeypatch.setattr(util, "_hash_block_size", 4)
    cfg = None if dry_run else tmp_archive
    seen = []

    def progress(path, nbytes):
        seen.append((path, nbytes))

    transfer.receive_file(cfg, "res_1", "f.wav", io.BytesIO(b"123456"), None, progress)
    assert seen == [("f.wav", 4), ("f.wav", 6)]
    seen.clear()
    entries = [("sub", None), ("sub/a", io.BytesIO(b"12345")), ("b", io.BytesIO(b"1"))]
    transfer.receive_directory(cfg, "res_2", "res_2", entries, None, progress)
    assert seen == [("sub/a", 4), ("sub/a", 5), ("b", 1)]


class FakeTape(io.RawIOBase):
    """Reads like a tape drive in variable-block mode: one block per read, and
    ENOMEM for any read smaller than the block."""

    def __init__(self, data: bytes, block_size: int):
        self.blocks = [
            data[i : i + block_size] for i in range(0, len(data), block_size)
        ]

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.blocks:
            return 0
        block = self.blocks[0]
        if len(buffer) < len(block):
            raise OSError(errno.ENOMEM, "Cannot allocate memory")
        self.blocks.pop(0)
        buffer[: len(block)] = block
        return len(block)


tape_members = [("res_1.wav", b"x" * 50_000), ("res_2", b"y" * 3000)]


def test_plain_tarfile_fails_on_tape():
    # why _BlockReader is needed: tarfile's small reads are refused by the drive
    tape = FakeTape(tar_bytes(tape_members), 10240)
    with pytest.raises(OSError) as err:
        tarfile.open(fileobj=tape, mode="r|*")
    assert err.value.errno == errno.ENOMEM


@pytest.mark.parametrize("block_size", [512, 10240, 262144])
def test_block_reader_reads_tape(block_size):
    tape = FakeTape(tar_bytes(tape_members), block_size)
    reader = transfer._BlockReader(tape, transfer.tape_read_size)
    with tarfile.open(fileobj=reader, mode="r|*") as tar:
        results = [
            (res.name, res.data.read())
            for res in transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
        ]
    assert results == tape_members


def raise_enomem(self, buffer):
    raise OSError(errno.ENOMEM, "Cannot allocate memory")


def test_open_tar_explains_large_tape_blocks(monkeypatch):
    # /dev/null is a character device, as a tape drive is
    monkeypatch.setattr(transfer._BlockReader, "readinto", raise_enomem)
    with pytest.raises(OSError, match="blocks larger than"):
        with transfer.open_tar("/dev/null"):
            pass


def test_open_tar_leaves_other_errors_alone(monkeypatch, tmp_path):
    path = tmp_path / "archive.tar"
    path.write_bytes(tar_bytes(tape_members))
    monkeypatch.setattr(transfer._BlockReader, "readinto", raise_enomem)
    with pytest.raises(OSError, match="Cannot allocate memory"):
        with transfer.open_tar(path):
            pass


def test_open_tar_file(tmp_path):
    path = tmp_path / "archive.tar"
    path.write_bytes(tar_bytes(tape_members))
    with transfer.open_tar(path) as tar:
        items = transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
        results = [(res.name, res.data.read()) for res in items]
    assert results == tape_members


def source_for(path, sha1):
    return transfer.Source(path.name, sha1=sha1, path=path, archive="arch")


export_names = {"tar": "export.tar", "zip": "export.zip", "dir": "export"}


@pytest.fixture(params=list(export_names))
def export_format(request):
    return request.param


def write_export(tmp_path, fmt, *sources, compress=False):
    """Writes sources to a new export; returns its path and a result per source."""
    out = tmp_path / export_names[fmt]
    results = []
    with transfer.open_export(out, compress=compress) as writer:
        for source in sources:
            try:
                results.append(transfer.write_resource(writer, source))
            except transfer.TransferError as err:
                results.append(err)
    return out, results


def export_contents(path):
    """Returns (name, contents) for everything in an export, None for directories."""
    if path.suffix == ".tar":
        with tarfile.open(path) as tar:
            return [
                (m.name, tar.extractfile(m).read() if m.isreg() else None)
                for m in tar.getmembers()
            ]
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            assert zf.testzip() is None
            return [
                (i.filename.rstrip("/"), None if i.is_dir() else zf.read(i))
                for i in zf.infolist()
            ]
    return [
        (p.relative_to(path).as_posix(), None if p.is_dir() else p.read_bytes())
        for p in transfer._walk(path)
    ]


@pytest.fixture
def dir_resource(tmp_path):
    root = tmp_path / "src" / "res_d"
    for rel, data in directory_contents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


dir_resource_members = [
    ("res_d", None),
    ("res_d/a.txt", b"first"),
    ("res_d/sub", None),
    ("res_d/sub/b.bin", b"second"),
    ("res_d/sub/deeper", None),
    ("res_d/sub/deeper/c", b""),
]


def test_export_file_resource(tmp_path, export_format):
    path = tmp_path / "res_1.wav"
    path.write_bytes(b"one")
    out, results = write_export(
        tmp_path, export_format, source_for(path, sha1_of(b"one"))
    )
    assert results == [True]
    assert export_contents(out) == [("res_1.wav", b"one")]


def test_export_directory_resource(tmp_path, export_format, dir_resource):
    sha1 = directory_hash(tmp_path)
    out, results = write_export(tmp_path, export_format, source_for(dir_resource, sha1))
    assert results == [True]
    assert export_contents(out) == dir_resource_members


def test_export_tar_reads_back(tmp_path, dir_resource):
    sha1 = directory_hash(tmp_path)
    out, _ = write_export(tmp_path, "tar", source_for(dir_resource, sha1))
    with transfer.open_tar(out) as tar:
        res = next(transfer.iter_tar_resources(tar, lambda id, m: {"name": id}))
        received = transfer.receive_directory(None, "res_d", res.name, res.data, sha1)
    assert received.verified


def test_export_zip_compressed(tmp_path):
    path = tmp_path / "res_1"
    path.write_bytes(b"x" * 10000)
    out, _ = write_export(tmp_path, "zip", source_for(path, None), compress=True)
    with zipfile.ZipFile(out) as zf:
        [info] = zf.infolist()
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.compress_size < info.file_size
    assert export_contents(out) == [("res_1", b"x" * 10000)]


def test_export_zip_stored_by_default(tmp_path):
    path = tmp_path / "res_1"
    path.write_bytes(b"x" * 10000)
    out, _ = write_export(tmp_path, "zip", source_for(path, None))
    with zipfile.ZipFile(out) as zf:
        assert zf.infolist()[0].compress_type == zipfile.ZIP_STORED


def test_export_without_registered_hash(tmp_path, export_format):
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    out, results = write_export(tmp_path, export_format, source_for(path, None))
    assert results == [False]
    assert export_contents(out) == [("res_1", b"one")]


def test_export_leaves_out_mismatched_file(tmp_path, export_format):
    paths = [tmp_path / name for name in ("res_1", "res_2", "res_3")]
    for path in paths:
        path.write_bytes(path.name.encode())
    out, results = write_export(
        tmp_path,
        export_format,
        source_for(paths[0], sha1_of(b"res_1")),
        source_for(paths[1], sha1_of(b"something else")),
        source_for(paths[2], sha1_of(b"res_3")),
    )
    assert results[0] is True and results[2] is True
    assert "don't match" in str(results[1])
    assert export_contents(out) == [("res_1", b"res_1"), ("res_3", b"res_3")]


def test_export_leaves_out_mismatched_directory(tmp_path, export_format, dir_resource):
    last = tmp_path / "res_2"
    last.write_bytes(b"two")
    out, results = write_export(
        tmp_path,
        export_format,
        source_for(dir_resource, sha1_of(b"wrong")),
        source_for(last, sha1_of(b"two")),
    )
    assert isinstance(results[0], transfer.TransferError)
    assert export_contents(out) == [("res_2", b"two")]


# the size of an export with nothing in it
empty_size = {"tar": tarfile.RECORDSIZE, "zip": 22}


def test_export_refuses_link_in_directory(tmp_path, export_format, dir_resource):
    (dir_resource / "link").symlink_to("a.txt")
    out, results = write_export(tmp_path, export_format, source_for(dir_resource, None))
    assert "not a file or directory" in str(results[0])
    assert export_contents(out) == []
    if export_format in empty_size:
        # nothing is left of the resource after the end of the file
        assert out.stat().st_size == empty_size[export_format]


@pytest.mark.skipif(os.getuid() == 0, reason="root can read anything")
def test_export_unreadable_file(tmp_path, export_format, dir_resource):
    (dir_resource / "sub" / "b.bin").chmod(0)
    out, results = write_export(tmp_path, export_format, source_for(dir_resource, None))
    assert "unable to read" in str(results[0])
    assert export_contents(out) == []


def test_export_file_that_shrinks(tmp_path, export_format, monkeypatch):
    path = tmp_path / "res_1"
    path.write_bytes(b"x" * 100)
    real_stat = transfer._stat

    def grown(path):
        st = list(real_stat(path))
        st[stat.ST_SIZE] += 10
        return os.stat_result(st)

    monkeypatch.setattr(transfer, "_stat", grown)
    out, results = write_export(tmp_path, export_format, source_for(path, None))
    assert "got shorter" in str(results[0])
    assert export_contents(out) == []


def test_export_without_a_copy(tmp_path, export_format):
    _, results = write_export(
        tmp_path, export_format, transfer.Source("res_1", error="no copy")
    )
    assert str(results[0]) == "no copy"


def test_export_directory_never_replaces(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    (out / "res_1").write_bytes(b"already here")
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    _, results = write_export(tmp_path, "dir", source_for(path, None))
    assert "already exists" in str(results[0])
    assert (out / "res_1").read_bytes() == b"already here"


def test_export_directory_keeps_leftover_partial(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    (out / ".res_1.partial").write_bytes(b"left over")
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    _, results = write_export(tmp_path, "dir", source_for(path, None))
    assert "left over from an earlier export" in str(results[0])
    assert (out / ".res_1.partial").read_bytes() == b"left over"


def test_export_directory_keeps_mtime(tmp_path):
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    os.utime(path, (1_000_000_000, 1_000_000_000))
    out, _ = write_export(tmp_path, "dir", source_for(path, None))
    assert (out / "res_1").stat().st_mtime == 1_000_000_000


def test_export_writable_copies(tmp_path, dir_resource):
    # resources in an archive are read-only; exported copies needn't be
    for path in [*transfer._walk(dir_resource), dir_resource]:
        path.chmod(0o555 if path.is_dir() else 0o444)
    out, _ = write_export(tmp_path, "dir", source_for(dir_resource, None))
    archive.remove(out / "res_d")
    for path in [*transfer._walk(dir_resource), dir_resource]:
        path.chmod(0o755 if path.is_dir() else 0o644)


@pytest.mark.parametrize("name", ["export.tar.gz", "export.tgz", "export.tar.xz"])
def test_export_refuses_compressed_tar(tmp_path, name):
    with pytest.raises(ValueError, match=r"use \.tar or \.zip"):
        with transfer.open_export(tmp_path / name):
            pass
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize("fmt", ["tar", "zip"])
def test_export_never_overwrites_file(tmp_path, fmt):
    out = tmp_path / export_names[fmt]
    out.write_bytes(b"already here")
    with pytest.raises(FileExistsError):
        with transfer.open_export(out):
            pass
    assert out.read_bytes() == b"already here"


def test_export_reports_progress(tmp_path, export_format, dir_resource):
    calls = []
    with transfer.open_export(tmp_path / export_names[export_format]) as writer:
        transfer.write_resource(
            writer,
            source_for(dir_resource, None),
            progress=lambda path, n: calls.append((path, n)),
        )
    assert calls == [("a.txt", 5), ("sub/b.bin", 6)]


def test_export_manifest(tmp_path, export_format):
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    manifest = {"resources": [{"name": "res_1", "path": "res_1"}]}
    out = tmp_path / export_names[export_format]
    with transfer.open_export(out) as writer:
        transfer.write_resource(writer, source_for(path, None))
        transfer.write_manifest(writer, manifest)
    contents = export_contents(out)
    if export_format != "dir":
        # written last, once the resources are known
        assert [name for name, _ in contents] == ["res_1", "manifest.json"]
    assert json.loads(dict(contents)["manifest.json"]) == manifest


def test_export_manifest_never_replaces(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    (out / "manifest.json").write_text("already here")
    with transfer.open_export(out) as writer:
        with pytest.raises(transfer.TransferError, match="already exists"):
            transfer.write_manifest(writer, {})
    assert (out / "manifest.json").read_text() == "already here"


def test_tar_resources_skip_manifest():
    # even if 'manifest' is a registered id
    members = [("res_1", b"one"), ("manifest.json", b"{}"), ("sub/manifest.json", b"")]
    assert read_tar(members, {"res_1", "manifest"}) == [
        ("res_1", "res_1", b"one"),
        ("manifest", "manifest.json", b""),
    ]


@respx.mock(assert_all_mocked=True)
def test_located_in(respx_mock, tmp_path):
    fake = FakeBulkLocations(
        respx_mock,
        [
            record("res_1", None, location("dest", tmp_path, "res_1")),
            record("res_2", None, location("other", tmp_path, "res_2")),
        ],
    )
    with httpx.Client() as session:
        found = transfer.located_in(
            session, base_url, ["res_1", "res_2", "res_3"], "dest"
        )
    assert found == {"res_1"}
    assert fake.requests[0]["archive"] == "dest"


@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_source_file(tmp_archive, tmp_path, dry_run):
    path = tmp_path / "res_1.wav"
    path.write_bytes(b"one")
    dest = None if dry_run else tmp_archive
    received = transfer.receive_source(dest, source_for(path, sha1_of(b"one")))
    assert received.verified
    if not dry_run:
        assert received.path.read_bytes() == b"one"
        assert received.path.name == "res_1.wav"


@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_source_directory(tmp_archive, tmp_path, dir_resource, dry_run):
    sha1 = directory_hash(tmp_path)
    dest = None if dry_run else tmp_archive
    received = transfer.receive_source(dest, source_for(dir_resource, sha1))
    assert received.verified
    if not dry_run:
        assert util.hash_directory(received.path) == sha1


@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_source_mismatch(tmp_archive, tmp_path, dir_resource, dry_run):
    dest = None if dry_run else tmp_archive
    with pytest.raises(transfer.TransferError, match="don't match"):
        transfer.receive_source(dest, source_for(dir_resource, sha1_of(b"wrong")))
    assert leftovers(tmp_archive) == []


@pytest.mark.skipif(os.getuid() == 0, reason="root can read anything")
@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_source_unreadable(tmp_archive, tmp_path, dir_resource, dry_run):
    (dir_resource / "sub" / "b.bin").chmod(0)
    dest = None if dry_run else tmp_archive
    with pytest.raises(transfer.TransferError):
        transfer.receive_source(dest, source_for(dir_resource, None))
    assert leftovers(tmp_archive) == []


def test_receive_source_refuses_link(tmp_archive, tmp_path, dir_resource):
    (dir_resource / "link").symlink_to("a.txt")
    with pytest.raises(transfer.TransferError, match="not a file or directory"):
        transfer.receive_source(tmp_archive, source_for(dir_resource, None))
    assert leftovers(tmp_archive) == []


def test_receive_directory_needs_policy(tmp_path):
    cfg = archive.create(tmp_path / "no_dirs", base_url)
    with pytest.raises(transfer.TransferError, match="doesn't allow directory"):
        transfer.receive_directory(cfg, "res_1", "res_1", directory_entries(), None)
    assert list((cfg["path"] / "resources").iterdir()) == []
