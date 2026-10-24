# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Write zip archives in a canonical form, so the same files always give the same
bytes (and the same hash).

The archive depends only on the (path, contents) pairs it's built from: entries
are sorted by path and stored without compression, with fixed timestamps and
permissions, no directory entries, extra fields, or comments, and ZIP64 records
only where the sizes or offsets need them. Members are stored so they can be
read in place, e.g. memory-mapped at their offset.
"""

import os
import struct
import zlib
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path

Source = Path | bytes

_CHUNK = 1 << 20
# sizes, offsets, and counts from these limits on go in ZIP64 records, and the
# field in the ordinary record holds the marker instead
_ZIP64_LIMIT = 0xFFFFFFFF
_ZIP64_COUNT_LIMIT = 0xFFFF
_MARKER = 0xFFFFFFFF
_COUNT_MARKER = 0xFFFF

_LOCAL_HEADER = struct.Struct("<4sHHHHHIIIHH")
_CENTRAL_HEADER = struct.Struct("<4sHHHHHHIIIHHHHHII")
_END = struct.Struct("<4sHHHHIIH")
_ZIP64_END = struct.Struct("<4sQHHIIQQQQ")
_ZIP64_LOCATOR = struct.Struct("<4sIQI")

# set for names that aren't ASCII, as Python's zipfile does
_UTF8_NAMES = 0x0800
_STORED = 0
# 1980-01-01 00:00:00, the earliest time a zip entry can have
_DOS_TIME = 0
_DOS_DATE = (1 << 5) | 1
_FILE_ATTRIBUTES = (0o100644 & 0xFFFF) << 16
_UNIX = 3


def write_zip(out_path: Path, members: Iterable[tuple[str, Source]]) -> Path:
    """Writes the members, (path in the archive, source) pairs, to a zip at out_path.

    A source is a file (read as it's written) or bytes. Paths are relative, use
    '/' as the separator, and can't contain '.' or '..' components or repeat.
    The zip is written to a .partial file beside out_path and renamed when it's
    complete. Returns out_path.

    Raises ValueError for a bad or repeated path, FileExistsError if out_path
    exists, and RuntimeError if a file changes size while it's being written.
    """
    out_path = Path(out_path)
    entries = sorted(
        ((_member_name(path), source) for path, source in members),
        key=lambda entry: entry[0],
    )
    for (a, _), (b, _) in pairwise(entries):
        if a == b:
            raise ValueError(f"'{a.decode()}' is in the archive more than once")
    if out_path.exists():
        raise FileExistsError(f"'{out_path}' already exists")
    partial = out_path.with_name(f".{out_path.name}.partial")
    try:
        with open(partial, "wb") as fp:
            central = [_write_member(fp, name, source) for name, source in entries]
            _write_central_directory(fp, central)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(partial, out_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return out_path


def zip_directory(
    src_dir: Path, out_path: Path, extra: Iterable[tuple[str, Source]] = ()
) -> Path:
    """Writes every file under src_dir, with paths relative to it, and the extra
    members to a canonical zip at out_path. Returns out_path.

    Raises ValueError if src_dir contains a symbolic link or out_path is inside
    src_dir, and the errors of write_zip.
    """
    src_dir = Path(src_dir)
    out_path = Path(out_path)
    if out_path.resolve().is_relative_to(src_dir.resolve()):
        raise ValueError(f"'{out_path}' can't be written inside '{src_dir}'")
    files = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"'{path}' is a symbolic link")
        if path.is_file():
            files.append((path.relative_to(src_dir).as_posix(), path))
    return write_zip(out_path, [*files, *extra])


def _member_name(path: str) -> bytes:
    """Returns path as a member name, or raises ValueError if it isn't a relative
    path with '/' separators and no empty, '.', or '..' components."""
    parts = path.split("/")
    if (
        not path
        or "\\" in path
        or "\0" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"'{path}' is not a relative path for a member of the zip")
    return path.encode("utf-8")


def _name_flags(name: bytes) -> int:
    return 0 if name.isascii() else _UTF8_NAMES


def _source_size(source: Source) -> int:
    return len(source) if isinstance(source, bytes) else Path(source).stat().st_size


def _write_member(fp, name: bytes, source: Source) -> tuple[bytes, int, int, int]:
    """Writes one member's local header and data. Returns (name, crc, size,
    offset) for the central directory."""
    offset = fp.tell()
    size = _source_size(source)
    zip64 = size >= _ZIP64_LIMIT
    extra = struct.pack("<HHQQ", 0x0001, 16, size, size) if zip64 else b""
    stored_size = _MARKER if zip64 else size
    version = 45 if zip64 else 20

    def header(crc: int) -> bytes:
        return _LOCAL_HEADER.pack(
            b"PK\x03\x04",
            version,
            _name_flags(name),
            _STORED,
            _DOS_TIME,
            _DOS_DATE,
            crc,
            stored_size,
            stored_size,
            len(name),
            len(extra),
        )

    fp.write(header(0))
    fp.write(name)
    fp.write(extra)
    if isinstance(source, bytes):
        crc = zlib.crc32(source)
        fp.write(source)
        written = size
    else:
        crc = written = 0
        with open(source, "rb") as src:
            while chunk := src.read(_CHUNK):
                crc = zlib.crc32(chunk, crc)
                fp.write(chunk)
                written += len(chunk)
    if written != size:
        raise RuntimeError(
            f"'{source}' changed size while it was being written to the zip"
        )
    end = fp.tell()
    fp.seek(offset)
    fp.write(header(crc))
    fp.seek(end)
    return name, crc, size, offset


def _write_central_directory(fp, members) -> None:
    start = fp.tell()
    for name, crc, size, offset in members:
        zip64_fields = []
        if size >= _ZIP64_LIMIT:
            zip64_fields += [size, size]
        if offset >= _ZIP64_LIMIT:
            zip64_fields.append(offset)
        extra = (
            struct.pack(
                f"<HH{len(zip64_fields)}Q", 0x0001, 8 * len(zip64_fields), *zip64_fields
            )
            if zip64_fields
            else b""
        )
        version = 45 if zip64_fields else 20
        stored_size = _MARKER if size >= _ZIP64_LIMIT else size
        fp.write(
            _CENTRAL_HEADER.pack(
                b"PK\x01\x02",
                (_UNIX << 8) | version,
                version,
                _name_flags(name),
                _STORED,
                _DOS_TIME,
                _DOS_DATE,
                crc,
                stored_size,
                stored_size,
                len(name),
                len(extra),
                0,
                0,
                0,
                _FILE_ATTRIBUTES,
                _MARKER if offset >= _ZIP64_LIMIT else offset,
            )
        )
        fp.write(name)
        fp.write(extra)
    end = fp.tell()
    count, size = len(members), end - start
    many, big, far = (
        count >= _ZIP64_COUNT_LIMIT,
        size >= _ZIP64_LIMIT,
        start >= _ZIP64_LIMIT,
    )
    if many or big or far:
        fp.write(
            _ZIP64_END.pack(
                b"PK\x06\x06",
                _ZIP64_END.size - 12,
                (_UNIX << 8) | 45,
                45,
                0,
                0,
                count,
                count,
                size,
                start,
            )
        )
        fp.write(_ZIP64_LOCATOR.pack(b"PK\x06\x07", 0, end, 1))
    fp.write(
        _END.pack(
            b"PK\x05\x06",
            0,
            0,
            _COUNT_MARKER if many else count,
            _COUNT_MARKER if many else count,
            _MARKER if big else size,
            _MARKER if far else start,
            0,
        )
    )


__all__ = ["write_zip", "zip_directory"]
