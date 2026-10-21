# -*- mode: python -*-
import io
import mmap
import zipfile

import pytest

from nbank import canonical_zip, zipped

DATA = bytes(range(256)) * 40
MEMBERS = [
    ("metadata.json", b'{"version": "1.0"}'),
    ("sub/2016_05_26_0000.abf", DATA),
    ("notes.txt", b""),
]


@pytest.fixture
def stored(tmp_path):
    return canonical_zip.write_zip(tmp_path / "stored.zip", MEMBERS)


@pytest.fixture
def compressed(tmp_path):
    path = tmp_path / "compressed.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in MEMBERS:
            zf.writestr(name, data)
    return path


def test_members(stored):
    assert zipped.members(stored) == [
        "metadata.json",
        "notes.txt",
        "sub/2016_05_26_0000.abf",
    ]


@pytest.mark.parametrize("name, data", MEMBERS)
def test_member_extent(stored, name, data):
    offset, size = zipped.member_extent(stored, name)
    assert size == len(data)
    assert stored.read_bytes()[offset : offset + size] == data


def test_member_can_be_memory_mapped(stored):
    offset, size = zipped.member_extent(stored, "sub/2016_05_26_0000.abf")
    # a mapping starts at a multiple of the allocation granularity, so map from
    # there and index past it, as np.memmap does
    start = offset - offset % mmap.ALLOCATIONGRANULARITY
    with (
        open(stored, "rb") as fp,
        mmap.mmap(
            fp.fileno(), offset - start + size, offset=start, access=mmap.ACCESS_READ
        ) as m,
    ):
        assert m[offset - start :] == DATA


def test_member_extent_skips_local_extra_field(tmp_path):
    path = tmp_path / "extra.zip"
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("a.abf")
        info.extra = b"\xfe\xca\x04\x00abcd"
        zf.writestr(info, DATA)
    offset, size = zipped.member_extent(path, "a.abf")
    assert path.read_bytes()[offset : offset + size] == DATA


def test_member_extent_refuses_compressed_members(compressed):
    with pytest.raises(ValueError, match="can't be read in place"):
        zipped.member_extent(compressed, "sub/2016_05_26_0000.abf")


def test_missing_member(stored):
    for call in (zipped.member_extent, zipped.open_member):
        with pytest.raises(KeyError):
            call(stored, "nothing.abf")


@pytest.mark.parametrize("which", ["stored", "compressed"])
def test_open_member(request, which):
    path = request.getfixturevalue(which)
    with zipped.open_member(path, "sub/2016_05_26_0000.abf") as fp:
        assert fp.seekable()
        assert fp.read(10) == DATA[:10]
        fp.seek(5000)
        assert fp.tell() == 5000
        assert fp.read(100) == DATA[5000:5100]
        fp.seek(-16, io.SEEK_END)
        assert fp.read() == DATA[-16:]
        assert fp.read() == b""
        fp.seek(0)
        assert fp.read() == DATA


def test_open_member_reads_only_its_member(stored):
    with zipped.open_member(stored, "notes.txt") as fp:
        assert fp.read() == b""
        fp.seek(100)
        assert fp.read(10) == b""
    with zipped.open_member(stored, "metadata.json") as fp:
        assert fp.read() == b'{"version": "1.0"}'


def test_open_member_closes_the_file(stored):
    fp = zipped.open_member(stored, "metadata.json")
    raw = fp.raw._fp
    fp.close()
    assert raw.closed


@pytest.mark.parametrize("which", ["stored", "compressed"])
def test_extract_member(request, which, tmp_path):
    path = request.getfixturevalue(which)
    out = zipped.extract_member(path, "sub/2016_05_26_0000.abf", tmp_path / "out")
    assert out == tmp_path / "out" / "sub" / "2016_05_26_0000.abf"
    assert out.read_bytes() == DATA


def test_open_member_releases_buffers(stored, monkeypatch):
    # readinto must not leave a view on the caller's buffer: PyPy's read()
    # resizes the buffer after readinto, which fails while a view exists
    # (BufferError). PyPy frees objects later than CPython, so keeping every view
    # alive shows the same thing here.
    kept = []

    def kept_memoryview(obj):
        view = memoryview(obj)
        kept.append(view)
        return view

    monkeypatch.setattr(zipped, "memoryview", kept_memoryview, raising=False)
    with zipped.open_member(stored, "sub/2016_05_26_0000.abf") as fp:
        buf = bytearray(100)
        assert fp.raw.readinto(buf) == 100
        del buf[10:]  # resizing, as PyPy's read() does
        assert bytes(buf) == DATA[:10]
    assert kept
