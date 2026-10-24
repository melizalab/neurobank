# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Moving resources into and out of neurobank archives.

find_sources finds readable copies in the archives on this host, to export or copy.
receive_file stores a resource read from any stream in an archive, checking its hash,
and add_location records the new copy.

"""

import errno
import hashlib
import io
import json
import os
import stat
import tarfile
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NamedTuple

import httpx
from httpx import Client

from nbank import archive as nbank_archive
from nbank import registry, util


@dataclass(frozen=True)
class Source:
    """A resource to transfer and a readable copy of it on this host.

    sha1 and filename come from the registry record. path and archive locate the copy;
    if there isn't one, they're None and error says why.
    """

    id: str
    sha1: str | None = None
    filename: str | None = None
    path: Path | None = None
    archive: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


def _find_local_copy(record: dict, alt_base: Path | None) -> Source:
    """Returns a Source for the first readable local copy among a record's locations."""
    fields = {
        "id": record["name"],
        "sha1": record.get("sha1"),
        "filename": record.get("filename"),
    }
    error = "no copy on this host"
    for location in record["locations"]:
        resource = util.parse_location(location, alt_base=alt_base)
        path = getattr(resource, "path", None)
        if path is None:
            continue
        if path.is_dir():
            error = f"'{path}' is a directory, which isn't a resource"
            continue
        if not os.access(path, os.R_OK):
            error = f"'{path}' is not readable"
            continue
        return Source(path=path, archive=location["archive_name"], **fields)
    return Source(error=error, **fields)


def find_sources(
    session: Client,
    registry_url: str,
    ids: Iterable[str],
    *,
    archive: str | None = None,
    alt_base: Path | None = None,
) -> Iterator[Source]:
    """Yields a Source for each distinct id, in order, locating a readable local copy.

    With archive, only copies in that archive are used. alt_base, if given, replaces the
    directory that contains each archive, for copies of archives on another host (see
    archive.Resource). Hashes aren't checked here.
    """
    ids = list(dict.fromkeys(ids))
    params = {} if archive is None else {"archive": archive}
    found = {}
    for i in range(0, len(ids), util.bulk_batch_size):
        url, query = registry.get_locations_bulk(
            registry_url, ids[i : i + util.bulk_batch_size], **params
        )
        for record in util.query_registry_bulk(session, url, query):
            found[record["name"]] = record
    for id in ids:
        record = found.get(id)
        if record is not None:
            yield _find_local_copy(record, alt_base)
        elif archive is not None:
            yield Source(id, error=f"not in archive '{archive}'")
        else:
            yield Source(id, error="not in the registry, or has no locations")


class TransferError(Exception):
    """A resource couldn't be transferred. Message says why."""


@dataclass(frozen=True)
class Received:
    """The result of receiving a resource.

    path is where it was stored, or None for a dry run. sha1 is the hash of the data
    received. verified is False if the registry has no hash to check against.
    """

    path: Path | None
    sha1: str
    verified: bool


def _partial_name(name: str) -> str:
    """Returns the name a resource is written under until it's verified."""
    return f".{name}.partial"


def partial_target(name: str) -> str | None:
    """Returns the name a staged .partial file was for, or None if name isn't one."""
    if name.startswith(".") and name.endswith(".partial") and len(name) > 9:
        return name[1 : -len(".partial")]
    return None


def _check_hash(sha1: str, registered: str | None) -> bool:
    """Returns False if registered (the registry's hash) is None, else True.

    Raises TransferError if sha1 doesn't match registered.
    """
    if registered is None:
        return False
    if sha1 != registered.lower():
        raise TransferError(
            f"contents don't match the registered hash ({sha1} != {registered})"
        )
    return True


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _destination(cfg: nbank_archive.ArchiveConfig, id: str, name: str) -> Path:
    """Returns the destination path; raises TransferError if the resource is stored."""
    try:
        existing = nbank_archive.resource_path(cfg, id, resolve_ext=True)
    except FileNotFoundError:
        pass
    else:
        raise TransferError(f"'{id}' is already in the archive at '{existing}'")
    target = nbank_archive.new_resource_path(cfg, id, name)
    try:
        target.parent.mkdir()
    except FileExistsError:
        pass
    except OSError as err:
        raise TransferError(str(err)) from err
    else:
        nbank_archive.permission_fixer(cfg)(target.parent)
    return target


def _move_into_place(partial: Path, target: Path) -> None:
    """Moves a verified file into place, never replacing anything."""
    try:
        os.link(partial, target)
    except FileExistsError:
        raise TransferError(f"'{target}' appeared while it was being written") from None
    except OSError:
        # the filesystem doesn't support hard links
        if target.exists():
            raise TransferError(
                f"'{target}' appeared while it was being written"
            ) from None
        partial.rename(target)
    else:
        partial.unlink()
    _fsync_dir(target.parent)


Progress = Callable[[str, int], None]


def _progress_for(progress: Progress | None, path: str) -> Callable[[int], None] | None:
    if progress is None:
        return None
    return lambda nbytes: progress(path, nbytes)


def receive_file(
    cfg: nbank_archive.ArchiveConfig | None,
    id: str,
    name: str,
    source: BinaryIO,
    sha1: str | None,
    progress: Progress | None = None,
) -> Received:
    """Stores a file resource read from source in an archive, checking its hash.

    cfg is the destination archive's config, or None for a dry run, which only hashes
    and checks the data. name is the source's file name, which supplies the extension.
    The data is staged beside its destination and moved into place only if it matches
    sha1, the registered hash (or sha1 is None); otherwise nothing is left behind.
    progress is called with name and the bytes read so far. Raises TransferError if the
    resource can't be stored, and passes TarReadError on.
    """
    report = _progress_for(progress, name)
    if cfg is None:
        digest = util.hash_stream(source, progress=report)
        return Received(None, digest, _check_hash(digest, sha1))
    target = _destination(cfg, id, name)
    partial = target.parent / _partial_name(target.name)
    created = False
    try:
        try:
            fp = open(partial, "xb")
        except FileExistsError:
            raise TransferError(
                f"'{partial}' is left over from an earlier transfer; remove it first"
            ) from None
        created = True
        with fp:
            digest = util.hash_stream(source, copy_to=fp, progress=report)
            fp.flush()
            os.fsync(fp.fileno())
        verified = _check_hash(digest, sha1)
        nbank_archive.permission_fixer(cfg)(partial)
        _move_into_place(partial, target)
        created = False
        return Received(target, digest, verified)
    except TarReadError:
        raise
    except OSError as err:
        raise TransferError(str(err)) from err
    finally:
        if created:
            partial.unlink(missing_ok=True)


# size of each read from a tar file or tape device
_tape_read_size = 1 << 20


class TarReadError(OSError):
    """The tar file couldn't be read any further. The message says how far in."""


class _MemberReader:
    """Reads a tar member, raising TarReadError if the tar file ends partway through it."""

    def __init__(self, stream: BinaryIO, name: str):
        self._stream = stream
        self.name = name

    def read(self, size: int = -1) -> bytes:
        try:
            return self._stream.read(size)
        except tarfile.ReadError as err:
            raise TarReadError(
                f"the tar file ends partway through '{self.name}' ({err})"
            ) from err

    def __getattr__(self, attr):
        return getattr(self._stream, attr)


class _DirectoryMember:
    """Stands in for a resource that's a directory in a tar file, which can't be
    received: reading it raises TransferError."""

    def read(self, size: int = -1) -> bytes:
        raise TransferError("is a directory, which isn't a resource")


class _BlockReader(io.RawIOBase):
    """Reads raw in fixed-size requests, however little the caller asks for.

    A tape in variable-block mode fails (ENOMEM) reads smaller than its blocks. Counts
    the bytes and blocks read, and raises read errors as TarReadError.
    """

    def __init__(self, raw: BinaryIO, size: int):
        self.raw = raw
        self.size = size
        self.nbytes = 0
        self.nblocks = 0
        self._buf = memoryview(b"")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        if not self._buf:
            try:
                data = self.raw.read(self.size) or b""
            except OSError as err:
                raise TarReadError(
                    err.errno,
                    f"{err.strerror} after reading {self.nbytes} bytes "
                    f"in {self.nblocks} blocks",
                ) from err
            self.nbytes += len(data)
            self.nblocks += bool(data)
            self._buf = memoryview(data)
        n = min(len(buffer), len(self._buf))
        buffer[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


class _FileReader(io.RawIOBase):
    """Reads and seeks a file, raising read errors as TarReadError."""

    def __init__(self, raw: BinaryIO):
        self.raw = raw

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.raw.seek(offset, whence)

    def tell(self) -> int:
        return self.raw.tell()

    def readinto(self, buffer) -> int:
        try:
            return self.raw.readinto(buffer)
        except OSError as err:
            raise TarReadError(
                err.errno, f"{err.strerror} at byte {self.raw.tell()}"
            ) from err


@contextmanager
def open_tar(path: str | Path) -> Iterator[tarfile.TarFile]:
    """Opens a tar file, tape device, or '-' (stdin) to read its members in order.

    Members that aren't read are seeked past in a file, but have to be read through on
    a tape or stdin. Errors reading a file or device are raised as TarReadError.
    """
    import sys

    if str(path) == "-":
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as tar:
            yield tar
        return
    with open(path, "rb", buffering=0) as raw:
        is_device = stat.S_ISCHR(os.fstat(raw.fileno()).st_mode)
        try:
            if raw.seekable() and not is_device:
                reader = io.BufferedReader(_FileReader(raw), _tape_read_size)
                mode = "r:*"
            else:
                reader = _BlockReader(raw, _tape_read_size)
                mode = "r|*"
            with tarfile.open(fileobj=reader, mode=mode) as tar:
                yield tar
        except OSError as err:
            if is_device and err.errno == errno.ENOMEM:
                raise TarReadError(
                    err.errno,
                    f"the tape has blocks larger than {_tape_read_size} bytes, "
                    "the largest this can read",
                ) from err
            raise


class TarResource(NamedTuple):
    """A registered resource read from a tar file; see iter_tar_resources.

    record is its registry record and name the member's base name. data is a stream of
    the file, and size its size in bytes.
    """

    record: dict
    name: str
    data: BinaryIO
    size: int


def iter_tar_resources(
    tar: tarfile.TarFile, lookup: Callable[[str, tarfile.TarInfo], dict | None]
) -> Iterator[TarResource]:
    """Yields a TarResource for each registered resource in a tar file, in order.

    lookup(id, member) returns the registry record for id, or None. A member is a
    resource if the stem of its base name is a registered id. Each resource's data has
    to be read before asking for the next, or it's skipped. Unregistered directories,
    special members, and a top-level manifest.json are ignored. A registered id that's
    a directory is yielded with data that raises TransferError when read, and its
    contents are skipped.
    """
    inside = None
    for member in tar:
        path = PurePosixPath(member.name)
        if inside is not None and path.is_relative_to(inside):
            continue
        inside = None
        if not (member.isdir() or member.isreg()) or path == PurePosixPath(
            manifest_name
        ):
            continue
        record = lookup(path.stem, member)
        if record is None:
            continue
        if member.isreg():
            data = _MemberReader(tar.extractfile(member), member.name)
            yield TarResource(record, path.name, data, member.size)
        else:
            inside = path
            yield TarResource(record, path.name, _DirectoryMember(), 0)


class _HashingReader:
    """Reads a file of known size for an export, hashing it.

    Read errors and files that end early raise TransferError.
    """

    def __init__(self, path: Path, size: int, progress: Callable[[int], None] | None):
        self.path = path
        self.size = size
        self.progress = progress
        self.nbytes = 0
        self._hash = hashlib.sha1()
        try:
            self._fp = open(path, "rb")
        except OSError as err:
            raise TransferError(f"unable to read '{path}': {err}") from err

    def read(self, size: int = -1) -> bytes:
        try:
            data = self._fp.read(size)
        except OSError as err:
            raise TransferError(f"unable to read '{self.path}': {err}") from err
        if len(data) < size and self.nbytes + len(data) < self.size:
            raise TransferError(f"'{self.path}' got shorter while it was being read")
        self._hash.update(data)
        self.nbytes += len(data)
        if self.progress is not None:
            self.progress(self.nbytes)
        return data

    def copy_to(self, dest: BinaryIO) -> None:
        """Writes the whole file to dest."""
        while self.nbytes < self.size:
            dest.write(self.read(min(_tape_read_size, self.size - self.nbytes)))

    def hexdigest(self) -> str:
        return self._hash.hexdigest()

    def close(self) -> None:
        self._fp.close()


def _stat(path: Path) -> os.stat_result:
    """Returns the status of a file to export.

    Raises TransferError if it can't be read or is anything else.
    """
    try:
        st = path.lstat()
    except OSError as err:
        raise TransferError(f"unable to read '{path}': {err.strerror}") from err
    if not stat.S_ISREG(st.st_mode):
        raise TransferError(f"'{path}' is not a file")
    return st


class ExportWriter:
    """Writes resources to an export, one at a time.

    Call begin(name) with the resource's file name, then add_file, then commit() to
    keep it or abort() to remove it. st is the source's stat result, for its mode and
    times, and add_file copies the file from reader. Errors writing the export raise
    OSError.
    """

    def begin(self, name: str) -> None:
        pass

    def add_file(self, name: str, st: os.stat_result, reader: _HashingReader) -> None:
        raise NotImplementedError

    def add_bytes(self, name: str, data: bytes) -> None:
        """Adds a file that isn't a resource, such as a manifest."""
        raise NotImplementedError

    def commit(self) -> None:
        pass

    def abort(self) -> None:
        raise NotImplementedError


class _TarWriter(ExportWriter):
    """Writes to a tar file open for writing to a regular file (mode 'w' or 'x')."""

    def __init__(self, tar: tarfile.TarFile):
        self.tar = tar

    def begin(self, name: str) -> None:
        self._offset, self._n_members = self.tar.offset, len(self.tar.members)

    def _info(self, name: str, st: os.stat_result) -> tarfile.TarInfo:
        # built by hand, as gettarinfo stores hard-linked files as links
        info = tarfile.TarInfo(name)
        info.mode = stat.S_IMODE(st.st_mode)
        info.mtime = int(st.st_mtime)
        info.uid, info.gid = st.st_uid, st.st_gid
        return info

    def add_file(self, name: str, st: os.stat_result, reader: _HashingReader) -> None:
        info = self._info(name, st)
        info.size = st.st_size
        self.tar.addfile(info, reader)

    def add_bytes(self, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.mode = 0o644
        info.mtime = int(time.time())
        info.size = len(data)
        self.tar.addfile(info, io.BytesIO(data))

    def abort(self) -> None:
        # tarfile can't remove members, so this uses its internals
        self.tar.fileobj.seek(self._offset)
        self.tar.fileobj.truncate()
        self.tar.offset = self._offset
        del self.tar.members[self._n_members :]


class _ZipWriter(ExportWriter):
    """Writes to a zip file open for writing to a regular file (mode 'w' or 'x')."""

    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf

    def begin(self, name: str) -> None:
        self._offset, self._n_members = self.zf.start_dir, len(self.zf.filelist)

    def _info(self, name: str, st: os.stat_result) -> zipfile.ZipInfo:
        # zip timestamps can't be earlier than 1980
        mtime = time.localtime(max(st.st_mtime, 315532800))
        info = zipfile.ZipInfo(name, mtime[:6])
        info.external_attr = (st.st_mode & 0xFFFF) << 16
        return info

    def add_file(self, name: str, st: os.stat_result, reader: _HashingReader) -> None:
        info = self._info(name, st)
        info.compress_type = self.zf.compression
        info.file_size = st.st_size
        with self.zf.open(info, "w", force_zip64=True) as dest:
            reader.copy_to(dest)

    def add_bytes(self, name: str, data: bytes) -> None:
        info = zipfile.ZipInfo(name, time.localtime()[:6])
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = self.zf.compression
        self.zf.writestr(info, data)

    def abort(self) -> None:
        # zipfile can't remove members, so this uses its internals
        for info in self.zf.filelist[self._n_members :]:
            del self.zf.NameToInfo[info.filename]
        del self.zf.filelist[self._n_members :]
        self.zf.fp.seek(self._offset)
        self.zf.fp.truncate()
        self.zf.start_dir = self._offset


class _DirectoryWriter(ExportWriter):
    """Writes to a directory, staging each resource and never replacing anything."""

    def __init__(self, root: Path):
        self.root = root
        self._partial: Path | None = None

    def begin(self, name: str) -> None:
        self._target = self.root / name
        if self._target.exists() or self._target.is_symlink():
            raise TransferError(f"'{self._target}' already exists")
        partial = self.root / _partial_name(name)
        if partial.exists() or partial.is_symlink():
            raise TransferError(
                f"'{partial}' is left over from an earlier export; remove it first"
            )
        self._partial = partial

    def add_file(self, name: str, st: os.stat_result, reader: _HashingReader) -> None:
        path = self._partial
        with open(path, "xb") as fp:
            reader.copy_to(fp)
            fp.flush()
            os.fsync(fp.fileno())
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    def add_bytes(self, name: str, data: bytes) -> None:
        with open(self._partial, "xb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())

    def commit(self) -> None:
        _move_into_place(self._partial, self._target)
        self._partial = None

    def abort(self) -> None:
        if self._partial is not None:
            self._partial.unlink(missing_ok=True)
        self._partial = None


@contextmanager
def open_export(path: Path, compress: bool = False) -> Iterator[ExportWriter]:
    """Opens an export: a tar file (.tar), a zip file (.zip), or else a directory.

    Yields an ExportWriter. A directory is created if needed; tar and zip files must not
    exist. compress deflates zip members. Raises ValueError for compressed tar and other
    archive extensions, and OSError if the export can't be created.
    """
    suffix = path.suffix.lower()
    if suffix in (".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z"):
        raise ValueError(f"can't export to a '{suffix}' file; use .tar or .zip")
    if suffix == ".tar":
        with tarfile.open(path, "x", copybufsize=_tape_read_size) as tar:
            yield _TarWriter(tar)
    elif suffix == ".zip":
        compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        with zipfile.ZipFile(path, "x", compression=compression) as zf:
            yield _ZipWriter(zf)
    else:
        path.mkdir(parents=True, exist_ok=True)
        yield _DirectoryWriter(path)


def write_resource(
    writer: ExportWriter, source: Source, progress: Progress | None = None
) -> bool:
    """Writes source, found by find_sources, to writer, from open_export.

    The resource's hash is checked as it's read. It's stored under its file name in the
    archive. If it can't be read or doesn't match, what was written is removed. progress is called with each file's path and the bytes
    read so far. Returns False if the registry has no hash to check. Raises
    TransferError for problems with the resource and OSError for problems writing the
    export.
    """
    if source.path is None:
        raise TransferError(source.error or "no copy to read")
    name = source.path.name
    writer.begin(name)

    def add_file(member: str, path: Path, st: os.stat_result, label: str) -> str:
        reader = _HashingReader(path, st.st_size, _progress_for(progress, label))
        try:
            writer.add_file(member, st, reader)
        finally:
            reader.close()
        return reader.hexdigest()

    try:
        digest = add_file(name, source.path, _stat(source.path), name)
        verified = _check_hash(digest, source.sha1)
        writer.commit()
        return verified
    except BaseException:
        writer.abort()
        raise


def located_in(
    session: Client, registry_url: str, ids: Iterable[str], archive: str
) -> set[str]:
    """Returns the ids in ids that the registry has a location for in archive."""
    ids = list(dict.fromkeys(ids))
    found = set()
    for i in range(0, len(ids), util.bulk_batch_size):
        url, query = registry.get_locations_bulk(
            registry_url, ids[i : i + util.bulk_batch_size], archive=archive
        )
        found.update(r["name"] for r in util.query_registry_bulk(session, url, query))
    return found


def receive_source(
    cfg: nbank_archive.ArchiveConfig | None,
    source: Source,
    progress: Progress | None = None,
) -> Received:
    """Stores a Source in an archive with receive_file.

    cfg is the destination archive's config, or None for a dry run, which only reads and
    checks. Errors reading the source raise TransferError, including in a dry run.
    """
    if source.path is None:
        raise TransferError(source.error or "no copy to read")
    name = source.path.name
    try:
        with open(source.path, "rb") as fp:
            return receive_file(cfg, source.id, name, fp, source.sha1, progress)
    except OSError as err:
        raise TransferError(f"unable to read '{source.path}': {err}") from err


# name of the manifest in an export, which tar readers skip
manifest_name = "manifest.json"


def write_manifest(writer: ExportWriter, manifest: dict) -> None:
    """Writes manifest to an export as JSON, under manifest_name.

    Raises TransferError if an export to a directory already has a manifest, and
    OSError if the export can't be written.
    """
    data = json.dumps(manifest, indent=2).encode("utf-8") + b"\n"
    writer.begin(manifest_name)
    try:
        writer.add_bytes(manifest_name, data)
        writer.commit()
    except BaseException:
        writer.abort()
        raise


def add_location(
    session: Client, registry_url: str, id: str, archive_name: str, path: Path
) -> None:
    """Records a copy stored at path as a location in archive_name.

    If the registry refuses, the copy is removed; if it can't be reached, the copy is
    kept for `nbank check archive` to sort out. Raises TransferError in both cases.
    """
    url, body = registry.add_location(registry_url, id, archive_name)
    try:
        r = session.post(url, json=body)
    except httpx.RequestError as err:
        raise TransferError(
            f"unable to contact the registry ({err}); '{path}' was stored but may "
            "not be registered, so run `nbank check archive` on this archive"
        ) from err
    if r.status_code != httpx.codes.CREATED:
        nbank_archive.remove(path)
        try:
            detail = "; ".join(registry.error_messages(r))
        except ValueError:
            detail = r.text
        raise TransferError(f"the registry refused the location ({detail})")


__all__: list[str] = []
