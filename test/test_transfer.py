# -*- mode: python -*-
import errno
import io
import json
import os
import stat
import tarfile
import threading
import zipfile

import httpx
import pytest
import respx

from nbank import archive, check, transfer, util
from test import running_as_root, skip_unless_posix
from test.test_registry import base_url, bulk_url

bulk_locations_url = bulk_url + "locations/"


@pytest.fixture
def tmp_archive(tmp_path):
    skip_unless_posix()
    return archive.create(tmp_path / "archive", base_url)


def store(cfg, tmp_path, name, contents="contents"):
    src = tmp_path / name
    src.write_text(contents)
    sha1 = util.hash(src)
    return archive.store_resource(cfg, src, id=name), sha1


def location(archive_name, root, name, scheme="neurobank", key=None):
    """A registry location; key defaults to name, as store() stores it."""
    return {
        "archive_name": archive_name,
        "scheme": scheme,
        "root": str(root),
        "resource_name": name,
        "key": key or name,
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
    assert source == transfer.Source("res_1", sha1=sha1, path=path, archive="arch")
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
def test_find_names_offline_copies(respx_mock, tmp_path):
    FakeBulkLocations(
        respx_mock,
        [
            record(
                "res_1",
                None,
                location("tape", "tape01:1", "res_1", scheme="tape"),
                location("elsewhere", tmp_path / "gone", "res_1"),
            )
        ],
    )
    [source] = find(["res_1"])
    assert source.error == (
        "no copy on this host; offline in tape (read it with `nbank archive import-tar`)"
    )


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


@pytest.mark.skipif(running_as_root, reason="root can read anything")
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


@respx.mock(assert_all_mocked=True)
def test_find_refuses_a_directory(respx_mock, tmp_archive):
    path = archive.resource_path(tmp_archive, "res_1")
    path.mkdir(parents=True)
    FakeBulkLocations(
        respx_mock,
        [record("res_1", None, location("arch", tmp_archive["path"], "res_1"))],
    )
    [source] = find(["res_1"])
    assert source.path is None
    assert source.error == f"'{path}' is a directory, which isn't a resource"


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
    leftover = stub / transfer._partial_name("res_1.wav")
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


def test_receive_file_dry_run(tmp_archive):
    data = b"the contents"
    received = transfer.receive_file(
        None, "res_1", "f.wav", io.BytesIO(data), sha1_of(data)
    )
    assert received == transfer.Received(None, sha1_of(data), True)
    assert not (tmp_archive["path"] / "resources" / "re").exists()
    with pytest.raises(transfer.TransferError, match="don't match"):
        transfer.receive_file(None, "res_1", "f.wav", io.BytesIO(data), sha1_of(b"x"))


add_location_url = f"{base_url}resources/res_1/locations/"


def stored_file(cfg):
    return transfer.receive_file(cfg, "res_1", "f.wav", io.BytesIO(b"x"), None).path


@respx.mock(assert_all_mocked=True)
def test_add_location(respx_mock, tmp_archive):
    path = stored_file(tmp_archive)
    route = respx_mock.post(add_location_url).respond(201, json={})
    with httpx.Client() as session:
        transfer.add_location(session, base_url, "res_1", "arch", path)
    assert json.loads(route.calls.last.request.content) == {
        "archive_name": "arch",
        "key": "res_1.wav",
    }
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
        (transfer._partial_name("res_1.wav"), "res_1.wav"),
        (transfer._partial_name("res_1"), "res_1"),
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


def read_tar(members, registered):
    """Runs iter_tar_resources over members; returns what it yields, read, or the
    error from reading it."""
    tar = tarfile.open(fileobj=io.BytesIO(tar_bytes(members)), mode="r|")

    def lookup(id, member):
        return {"name": id} if id in registered else None

    results = []
    for res in transfer.iter_tar_resources(tar, lookup):
        try:
            data = res.data.read()
        except transfer.TransferError as err:
            data = str(err)
        results.append((res.record["name"], res.name, data))
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
        ("res_2", "res_2", "is a directory, which isn't a resource"),
    ]


def test_tar_resources_directory():
    members = [
        ("unregistered_dir", None),
        ("unregistered_dir/res_u", b"inside an unregistered directory"),
        ("res_d", None),
        ("res_d/sub", None),
        ("res_d/sub/res_f", b"inside"),
        ("res_d/link", "link"),
        ("res_f.txt", b"after"),
    ]
    # the contents of a registered directory are skipped, even registered ids
    assert read_tar(members, {"res_d", "res_f", "res_u"}) == [
        ("res_u", "res_u", b"inside an unregistered directory"),
        ("res_d", "res_d", "is a directory, which isn't a resource"),
        ("res_f", "res_f.txt", b"after"),
    ]


def test_open_tar_from_stdin(monkeypatch):
    # a pipe can't seek, like a tape drive
    read_fd, write_fd = os.pipe()

    # from a thread, as the tar is larger than a Windows pipe holds
    def write():
        with open(write_fd, "wb") as fp:
            fp.write(tar_bytes([("res_1.wav", b"one")]))

    writer = threading.Thread(target=write)
    writer.start()

    class Stdin:
        buffer = open(read_fd, "rb")

    monkeypatch.setattr("sys.stdin", Stdin)
    with Stdin.buffer, transfer.open_tar("-") as tar:
        items = transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
        results = [(res.name, res.data.read()) for res in items]
    writer.join()
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
    reader = transfer._BlockReader(tape, transfer._tape_read_size)
    with tarfile.open(fileobj=reader, mode="r|*") as tar:
        results = [
            (res.name, res.data.read())
            for res in transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
        ]
    assert results == tape_members


def raise_enomem(self, buffer):
    raise OSError(errno.ENOMEM, "Cannot allocate memory")


@pytest.mark.posix
def test_open_tar_explains_large_tape_blocks(monkeypatch):
    # /dev/null is a character device, as a tape drive is
    monkeypatch.setattr(transfer._BlockReader, "readinto", raise_enomem)
    with pytest.raises(OSError, match="blocks larger than"):
        with transfer.open_tar("/dev/null"):
            pass


def test_open_tar_leaves_other_errors_alone(monkeypatch, tmp_path):
    path = tmp_path / "archive.tar"
    path.write_bytes(tar_bytes(tape_members))
    monkeypatch.setattr(transfer._FileReader, "readinto", raise_enomem)
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
    """Returns (name, contents) for everything in an export."""
    if path.suffix == ".tar":
        with tarfile.open(path) as tar:
            return [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            assert zf.testzip() is None
            return [(i.filename, zf.read(i)) for i in zf.infolist()]
    return [(p.name, p.read_bytes()) for p in sorted(path.iterdir())]


def test_export_file_resource(tmp_path, export_format):
    path = tmp_path / "res_1.wav"
    path.write_bytes(b"one")
    out, results = write_export(
        tmp_path, export_format, source_for(path, sha1_of(b"one"))
    )
    assert results == [True]
    assert export_contents(out) == [("res_1.wav", b"one")]


def test_export_tar_reads_back(tmp_path):
    path = tmp_path / "res_1.wav"
    path.write_bytes(b"one")
    out, _ = write_export(tmp_path, "tar", source_for(path, sha1_of(b"one")))
    with transfer.open_tar(out) as tar:
        res = next(transfer.iter_tar_resources(tar, lambda id, m: {"name": id}))
        received = transfer.receive_file(
            None, "res_1", res.name, res.data, sha1_of(b"one")
        )
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


empty_size = {"tar": tarfile.RECORDSIZE, "zip": 22}


@pytest.mark.parametrize(
    "kind", [pytest.param("link", marks=pytest.mark.posix), "directory"]
)
def test_export_refuses_what_isnt_a_file(tmp_path, export_format, kind):
    path = tmp_path / "res_1"
    if kind == "link":
        (tmp_path / "target").write_bytes(b"one")
        path.symlink_to("target")
    else:
        path.mkdir()
    out, results = write_export(tmp_path, export_format, source_for(path, None))
    assert "is not a file" in str(results[0])
    assert export_contents(out) == []
    if export_format in empty_size:
        # nothing is left of the resource after the end of the file
        assert out.stat().st_size == empty_size[export_format]


@pytest.mark.posix
@pytest.mark.skipif(running_as_root, reason="root can read anything")
def test_export_unreadable_file(tmp_path, export_format):
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    path.chmod(0)
    out, results = write_export(tmp_path, export_format, source_for(path, None))
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


@pytest.mark.posix
def test_export_writable_copies(tmp_path):
    # resources in an archive are read-only; exported copies needn't be
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    path.chmod(0o444)
    out, _ = write_export(tmp_path, "dir", source_for(path, None))
    assert os.access(out / "res_1", os.W_OK) or os.getuid() == 0


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


def test_export_reports_progress(tmp_path, export_format):
    path = tmp_path / "res_1"
    path.write_bytes(b"first")
    calls = []
    with transfer.open_export(tmp_path / export_names[export_format]) as writer:
        transfer.write_resource(
            writer,
            source_for(path, None),
            progress=lambda path, n: calls.append((path, n)),
        )
    assert calls == [("res_1", 5)]


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
def test_receive_source_mismatch(tmp_archive, tmp_path, dry_run):
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    dest = None if dry_run else tmp_archive
    with pytest.raises(transfer.TransferError, match="don't match"):
        transfer.receive_source(dest, source_for(path, sha1_of(b"wrong")))
    assert leftovers(tmp_archive) == []


@pytest.mark.skipif(running_as_root, reason="root can read anything")
@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_source_unreadable(tmp_archive, tmp_path, dry_run):
    path = tmp_path / "res_1"
    path.write_bytes(b"one")
    path.chmod(0)
    dest = None if dry_run else tmp_archive
    with pytest.raises(transfer.TransferError, match="unable to read"):
        transfer.receive_source(dest, source_for(path, None))
    assert leftovers(tmp_archive) == []


def test_receive_source_refuses_a_directory(tmp_archive, tmp_path):
    path = tmp_path / "res_1"
    path.mkdir()
    with pytest.raises(transfer.TransferError, match="unable to read"):
        transfer.receive_source(tmp_archive, source_for(path, None))
    assert leftovers(tmp_archive) == []


class FailingFile(io.RawIOBase):
    """A file that fails with EIO once limit bytes have been read, like a tape
    with a damaged stretch."""

    def __init__(self, path, limit):
        self._fp = io.FileIO(path, "rb")
        self.limit = limit
        self.nbytes = 0

    def fileno(self):
        return self._fp.fileno()

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.nbytes >= self.limit:
            raise OSError(errno.EIO, "Input/output error")
        # copied, not read through a view, which PyPy may not release before
        # the caller resizes buffer
        data = self._fp.read(min(len(buffer), self.limit - self.nbytes))
        buffer[: len(data)] = data
        self.nbytes += len(data)
        return len(data)

    def close(self):
        self._fp.close()
        super().close()


def test_tar_read_error_says_where(failing_tar):
    path = failing_tar(tape_members, 3 * 10240)
    with pytest.raises(transfer.TarReadError) as err:
        with transfer.open_tar(path) as tar:
            for res in transfer.iter_tar_resources(tar, lambda id, m: {"name": id}):
                res.data.read()
    assert err.value.errno == errno.EIO
    assert "after reading 30720 bytes in 3 blocks" in str(err.value)


@pytest.mark.parametrize("dry_run", [False, True])
def test_receive_passes_on_tar_read_error(failing_tar, tmp_archive, dry_run):
    path = failing_tar(tape_members, 3 * 10240)
    dest = None if dry_run else tmp_archive
    with pytest.raises(transfer.TarReadError):
        with transfer.open_tar(path) as tar:
            res = next(transfer.iter_tar_resources(tar, lambda id, m: {"name": id}))
            transfer.receive_file(dest, "res_1", res.name, res.data, None)
    assert leftovers(tmp_archive) == []


def truncated_tar(tmp_path, members, keep):
    """Writes a tar file of members cut off after keep bytes, as when a tape
    runs out partway through it."""
    path = tmp_path / "truncated.tar"
    path.write_bytes(tar_bytes(members)[:keep])
    return path


@pytest.mark.parametrize("dry_run", [False, True])
def test_truncated_member(tmp_path, tmp_archive, dry_run):
    # headers are 512 bytes; res_1.wav's 50000 bytes are padded to 50176
    path = truncated_tar(tmp_path, tape_members, 512 + 50176 + 512 + 1000)
    dest = None if dry_run else tmp_archive
    with transfer.open_tar(path) as tar:
        resources = transfer.iter_tar_resources(tar, lambda id, m: {"name": id})
        first = next(resources)
        transfer.receive_file(dest, "res_1", first.name, first.data, None)
        second = next(resources)
        with pytest.raises(transfer.TarReadError, match="res_2"):
            transfer.receive_file(dest, "res_2", second.name, second.data, None)
    assert leftovers(tmp_archive) == []


class CountingFile(io.FileIO):
    """A file that counts the bytes read from it."""

    nbytes = 0

    def read(self, size=-1):
        data = super().read(size)
        CountingFile.nbytes += len(data)
        return data

    def readinto(self, buffer):
        n = super().readinto(buffer)
        CountingFile.nbytes += n or 0
        return n


def test_open_tar_file_skips_without_reading(tmp_path, monkeypatch):
    path = tmp_path / "archive.tar"
    path.write_bytes(tar_bytes([("skipped", b"x" * 10_000_000), ("res_1", b"one")]))
    monkeypatch.setattr(
        transfer, "open", lambda p, *args, **kwargs: CountingFile(p), raising=False
    )
    CountingFile.nbytes = 0
    with transfer.open_tar(path) as tar:
        lookup = {"res_1": {"name": "res_1"}}.get
        [res] = [
            r.data.read()
            for r in transfer.iter_tar_resources(tar, lambda i, m: lookup(i))
        ]
    assert res == b"one"
    # the skipped member is seeked past, apart from what's buffered (about 1 MiB)
    assert CountingFile.nbytes < 4_000_000


class SeekableFailingFile(FailingFile):
    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        return self._fp.seek(offset, whence)

    def tell(self):
        return self._fp.tell()


def test_file_read_error_says_where(tmp_path, monkeypatch):
    path = tmp_path / "damaged.tar"
    path.write_bytes(tar_bytes(tape_members))
    monkeypatch.setattr(
        transfer,
        "open",
        lambda p, *args, **kwargs: SeekableFailingFile(p, 20_000),
        raising=False,
    )
    with pytest.raises(transfer.TarReadError, match="Input/output error at byte"):
        with transfer.open_tar(path) as tar:
            for res in transfer.iter_tar_resources(tar, lambda id, m: {"name": id}):
                res.data.read()
