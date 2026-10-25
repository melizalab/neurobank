# -*- mode: python -*-
import hashlib
import io
import os
import shutil
import struct
import subprocess
import zipfile
import zlib

import pytest

from nbank import canonical_zip

MEMBERS = [("metadata.json", b'{"version": "1.0"}'), ("sub/a.abf", b"x" * 1000)]


def sha1(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


def stream_members(data):
    """Reads the members from the local headers alone, in order, as a streaming
    reader does. Returns {name: contents} and the offset where they end."""
    members, pos = {}, 0
    while data[pos : pos + 4] == b"PK\x03\x04":
        (_, _, flags, method, _, _, crc, csize, usize, nlen, xlen) = struct.unpack_from(
            "<4sHHHHHIIIHH", data, pos
        )
        name = data[pos + 30 : pos + 30 + nlen].decode()
        extra = data[pos + 30 + nlen : pos + 30 + nlen + xlen]
        if csize == 0xFFFFFFFF:
            _, _, usize, csize = struct.unpack_from("<HHQQ", extra)
        assert not flags & 0x08, f"{name} has a data descriptor"
        assert method == 0, f"{name} is compressed"
        start = pos + 30 + nlen + xlen
        contents = data[start : start + csize]
        assert len(contents) == usize == csize
        assert zlib.crc32(contents) == crc, f"{name}: wrong CRC in the local header"
        members[name] = contents
        pos = start + csize
    return members, pos


@pytest.fixture
def written(tmp_path):
    def write(members, name="out.zip"):
        return canonical_zip.write_zip(tmp_path / name, members)

    return write


def test_zip_can_be_read_back(written):
    out = written(MEMBERS)
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        assert {i.filename: zf.read(i) for i in zf.infolist()} == dict(MEMBERS)
        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.external_attr >> 16 == 0o100644
            assert info.flag_bits == 0  # no data descriptor; names are ASCII
            assert info.extra == b""


def test_names_that_arent_ascii_are_utf8(written):
    with zipfile.ZipFile(written([("café.abf", b"x")])) as zf:
        [info] = zf.infolist()
        assert info.filename == "café.abf"
        assert info.flag_bits == 0x0800


def reference_zip(members):
    """The same archive written by Python's zipfile, with the canonical settings"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


@pytest.mark.parametrize("members", [MEMBERS, [], [("café.abf", b"x")]])
def test_output_matches_pythons_zipfile(written, members):
    assert written(members).read_bytes() == reference_zip(members)


def test_members_can_be_read_by_streaming(written):
    data = written(MEMBERS).read_bytes()
    members, end = stream_members(data)
    assert members == dict(MEMBERS)
    assert data[end : end + 4] == b"PK\x01\x02"  # the central directory follows


@pytest.mark.skipif(
    shutil.which("unzip") is None
    or "Info-ZIP"
    not in subprocess.run(["unzip", "-v"], capture_output=True, text=True).stdout,
    reason="needs Info-ZIP unzip",
)
def test_unzip_accepts_zip(written):
    result = subprocess.run(
        ["unzip", "-t", written(MEMBERS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_output_depends_only_on_paths_and_contents(tmp_path, written):
    first = written(MEMBERS, "first.zip")
    second = written(list(reversed(MEMBERS)), "second.zip")
    assert first.read_bytes() == second.read_bytes()


def test_output_is_pinned(written):
    # the canonical form is what makes hashes reproducible; changing it changes
    # the hash of every zip
    assert sha1(written(MEMBERS)) == "83f708e2fc6e496112e3100d12535ae87155f08b"


def test_zip_directory(tmp_path):
    src = tmp_path / "recording"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.abf").write_bytes(b"x" * 1000)
    (src / "notes.txt").write_bytes(b"notes")
    out = canonical_zip.zip_directory(
        src, tmp_path / "rec.zip", extra=[("metadata.json", b"{}")]
    )
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["metadata.json", "notes.txt", "sub/a.abf"]
    # timestamps and permissions of the files don't matter
    os.utime(src / "notes.txt", (0, 0))
    (src / "sub" / "a.abf").chmod(0o600)
    again = canonical_zip.zip_directory(
        src, tmp_path / "again.zip", extra=[("metadata.json", b"{}")]
    )
    assert again.read_bytes() == out.read_bytes()


@pytest.mark.parametrize(
    "path",
    ["", "/abs.abf", "../a.abf", "a/../b.abf", "a//b.abf", "a/", "./a.abf", "a\\b.abf"],
)
def test_bad_paths_are_refused(written, tmp_path, path):
    with pytest.raises(ValueError, match="not a relative path"):
        written([(path, b"x")])
    assert list(tmp_path.iterdir()) == []


def test_repeated_paths_are_refused(written):
    with pytest.raises(ValueError, match="more than once"):
        written([("a.abf", b"x"), ("a.abf", b"y")])


def test_existing_output_is_refused(written, tmp_path):
    (tmp_path / "out.zip").write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        written(MEMBERS)
    assert (tmp_path / "out.zip").read_bytes() == b"existing"


def test_failed_write_leaves_nothing(written, tmp_path):
    with pytest.raises(FileNotFoundError):
        written([("a.abf", tmp_path / "missing.abf")])
    assert list(tmp_path.iterdir()) == []


def test_file_that_changes_size_is_an_error(written, tmp_path, monkeypatch):
    src = tmp_path / "a.abf"
    src.write_bytes(b"x" * 100)
    # the size when the header was written was different from what was read
    monkeypatch.setattr(canonical_zip, "_source_size", lambda source: 50)
    with pytest.raises(RuntimeError, match="changed size"):
        written([("a.abf", src)], "out.zip")
    assert not (tmp_path / "out.zip").exists()
    assert not (tmp_path / ".out.zip.partial").exists()


@pytest.mark.posix
def test_zip_directory_refuses_symlinks(tmp_path):
    src = tmp_path / "recording"
    src.mkdir()
    (src / "a.abf").write_bytes(b"x")
    (src / "link.abf").symlink_to(src / "a.abf")
    with pytest.raises(ValueError, match="symbolic link"):
        canonical_zip.zip_directory(src, tmp_path / "rec.zip")


def test_zip_directory_refuses_output_inside_it(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        canonical_zip.zip_directory(tmp_path, tmp_path / "rec.zip")


# ZIP64, with the limits lowered so the tests don't need files of 4 GiB


@pytest.fixture
def small_zip64_limits(monkeypatch):
    monkeypatch.setattr(canonical_zip, "_ZIP64_LIMIT", 500)
    monkeypatch.setattr(canonical_zip, "_ZIP64_COUNT_LIMIT", 3)


@pytest.mark.usefixtures("small_zip64_limits")
@pytest.mark.parametrize(
    "members",
    [
        pytest.param([("big.abf", b"x" * 1000)], id="large-member"),
        pytest.param([("a.abf", b"x" * 400), ("b.abf", b"y" * 400)], id="large-offset"),
        pytest.param([(f"{i}.abf", b"x") for i in range(5)], id="many-members"),
    ],
)
def test_zip64(written, members):
    out = written(members)
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        assert {i.filename: zf.read(i) for i in zf.infolist()} == dict(members)
    assert stream_members(out.read_bytes())[0] == dict(members)


def test_no_zip64_records_when_not_needed(written):
    data = written(MEMBERS).read_bytes()
    assert b"PK\x06\x06" not in data
