# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: BSD-3-Clause
"""Functions for interacting with zipped resources without unpacking them.

Members stored without compression (as nbank.canonical_zip writes them) can be read
or memory-mapped directly from the zip file: member_extent gives where a member's
data starts and how long it is, e.g. for np.memmap(path, offset=offset, ...).
open_member gives a file object for any member, and extract_member writes one out
for libraries that only read files by path.

"""

import io
import struct
import zipfile
from pathlib import Path
from typing import BinaryIO

_LOCAL_HEADER = struct.Struct("<4sHHHHHIIIHH")
_ENCRYPTED = 0x1


def members(path: Path) -> list[str]:
    """Returns the names of the zip's members, in the order they're stored."""
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def member_extent(path: Path, name: str) -> tuple[int, int]:
    """Returns (offset, size): where the data of member name starts in the zip file,
    and its length in bytes.

    Raises KeyError if there's no such member, and ValueError if the member is
    compressed or encrypted, so its bytes in the file aren't its contents.
    """
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(name)
        if info.compress_type != zipfile.ZIP_STORED or info.flag_bits & _ENCRYPTED:
            raise ValueError(
                f"'{name}' is compressed or encrypted, so it can't be read in place"
            )
        with open(path, "rb") as fp:
            fp.seek(info.header_offset)
            header = fp.read(_LOCAL_HEADER.size)
        if len(header) < _LOCAL_HEADER.size:
            raise zipfile.BadZipFile(f"'{name}' has a truncated local header")
        signature, *_, name_length, extra_length = _LOCAL_HEADER.unpack(header)
        if signature != b"PK\x03\x04":
            raise zipfile.BadZipFile(f"'{name}' has no local header where expected")
        # the local header's extra field can differ from the central directory's
        offset = info.header_offset + _LOCAL_HEADER.size + name_length + extra_length
        return offset, info.file_size


def open_member(path: Path, name: str) -> BinaryIO:
    """Returns a read-only, seekable file object for member name.

    A stored member is read straight from the zip file, so seeking is cheap; a
    compressed one is decompressed as it's read. Raises KeyError if there's no such
    member.
    """
    try:
        offset, size = member_extent(path, name)
    except ValueError:
        return zipfile.ZipFile(path).open(name)
    return io.BufferedReader(_Slice(open(path, "rb"), offset, size))


def extract_member(path: Path, name: str, dest_dir: Path) -> Path:
    """Writes member name to dest_dir, under its path in the zip, and returns the
    path written. Raises KeyError if there's no such member."""
    with zipfile.ZipFile(path) as zf:
        return Path(zf.extract(name, dest_dir))


class _Slice(io.RawIOBase):
    """The bytes from offset to offset + size of an open file, as a file."""

    def __init__(self, fp: BinaryIO, offset: int, size: int):
        self._fp = fp
        self._offset = offset
        self._size = size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_CUR:
            pos += self._pos
        elif whence == io.SEEK_END:
            pos += self._size
        elif whence != io.SEEK_SET:
            raise ValueError(f"invalid whence ({whence})")
        if pos < 0:
            raise ValueError(f"negative seek position {pos}")
        self._pos = pos
        return pos

    def readinto(self, buffer) -> int:
        count = max(0, min(len(buffer), self._size - self._pos))
        if count == 0:
            return 0
        self._fp.seek(self._offset + self._pos)
        # released explicitly: a view left for the garbage collector (as on PyPy)
        # keeps the caller from resizing the buffer
        with memoryview(buffer) as view, view[:count] as part:
            n = self._fp.readinto(part)
        self._pos += n
        return n

    def close(self) -> None:
        if not self.closed:
            self._fp.close()
        super().close()


__all__ = ["extract_member", "member_extent", "members", "open_member"]
