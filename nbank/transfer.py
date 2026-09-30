# -*- mode: python -*-
"""Moving resources into and out of neurobank archives

The two directions work differently:

- Reading out: find_sources finds a readable copy of each requested resource
  in the neurobank archives on this host, to be exported (to a tar, zip, or
  directory) or copied into another archive.
- Writing in: receive_file and receive_directory store a resource in a
  neurobank archive, checking it against its registered hash. The data can come
  from anything that can be read as a stream: a file found by find_sources, a
  tar member read from tape or stdin, or a download. add_location then records
  the new copy in the registry.

Copyright (C) 2026 Dan Meliza <dan@meliza.org>
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
    """A resource to be transferred and where it can be read on this host.

    sha1 and filename come from the registry record. path is a readable local
    copy and archive the name of the archive that holds it; if there's no such
    copy, both are None and error says why.
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


def _unreadable(path: Path) -> Path | None:
    """Returns path, or a file or directory inside it, that can't be read; else None."""
    if path.is_dir():
        for item in [path, *path.rglob("*")]:
            perm = (os.R_OK | os.X_OK) if item.is_dir() else os.R_OK
            if not os.access(item, perm):
                return item
        return None
    return None if os.access(path, os.R_OK) else path


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
        unreadable = _unreadable(path)
        if unreadable is not None:
            error = f"'{unreadable}' is not readable"
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
    """Finds a readable copy of each resource in ids, in neurobank archives on this host.

    Yields one Source per distinct id, in the order requested. A Source without
    a path has an error saying why: the id isn't registered or has no locations
    (the registry doesn't distinguish these in a bulk request), or no location
    is on this host and readable. Set archive to use only copies in that archive.
    Only checks that files can be read; hashes are checked when they're copied.
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

    path is where it was stored, or None for a dry run. sha1 is the hash of the
    data received. verified is True if the registry has a hash and it matched,
    and False if the registry has no hash to check against.
    """

    path: Path | None
    sha1: str
    verified: bool


def _partial_name(name: str) -> str:
    """Returns the name a resource is written under until it's verified."""
    return f".{name}.partial"


def partial_target(name: str) -> str | None:
    """Returns the name a temporary transfer file was going to be given, or None.

    This is the inverse of _partial_name, for recognizing files left over from
    transfers that didn't finish.
    """
    if name.startswith(".") and name.endswith(".partial") and len(name) > 9:
        return name[1 : -len(".partial")]
    return None


def _check_hash(sha1: str, registered: str | None) -> bool:
    """Returns whether sha1 was checked; raises TransferError if it doesn't match."""
    if registered is None:
        return False
    if sha1 != registered.lower():
        raise TransferError(
            f"contents don't match the registered hash ({sha1} != {registered})"
        )
    return True


def _entry_path(relpath: str) -> PurePosixPath:
    """Returns relpath if it names something inside a directory resource.

    Raises TransferError for empty, absolute, or '..' paths, which could write
    outside the resource.
    """
    path = PurePosixPath(relpath)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath():
        raise TransferError(f"'{relpath}' is not a path inside the resource")
    return path


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _destination(cfg: nbank_archive.ArchiveConfig, id: str, name: str) -> Path:
    """Returns where the resource will be stored, or raises TransferError if it's there."""
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
    """Moves a verified file or directory to its name in the archive, never replacing one."""
    if partial.is_dir():
        if target.exists():
            raise TransferError(f"'{target}' appeared while it was being written")
        partial.rename(target)
    else:
        try:
            os.link(partial, target)
        except FileExistsError:
            raise TransferError(
                f"'{target}' appeared while it was being written"
            ) from None
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
    """Stores a file resource read from source in a neurobank archive, checking its hash.

    name is the source's file name, which supplies the extension. The data is
    written to a temporary name beside its destination and hashed as it's
    written. It's moved into place only if the hash matches sha1 (the registered
    hash) or sha1 is None; otherwise, or on any error, the temporary file is
    removed. The archive's permission policy is applied before the move.

    If cfg is None, this is a dry run: the data is hashed and checked, and
    nothing is written. progress, if given, is called as data is read with name
    and the number of bytes read so far. Raises TransferError if the resource
    can't be stored.
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
    except OSError as err:
        raise TransferError(str(err)) from err
    finally:
        if created:
            partial.unlink(missing_ok=True)


def receive_directory(
    cfg: nbank_archive.ArchiveConfig | None,
    id: str,
    name: str,
    entries: Iterable[tuple[str, BinaryIO | None]],
    sha1: str | None,
    progress: Progress | None = None,
) -> Received:
    """Stores a directory resource in a neurobank archive, checking its hash.

    entries are (path, source) pairs, one for each file in the directory, with
    paths relative to the directory. A source of None stands for a directory,
    which is created but doesn't contribute to the hash. The files are written
    under a temporary directory beside the destination and hashed as they're
    written, and the whole directory is moved into place only if its hash
    matches sha1 or sha1 is None. Paths that are absolute, contain '..', or
    repeat are refused. On any error, the temporary directory is removed.

    If cfg is None, this is a dry run: the files are hashed and checked, and
    nothing is written. progress, if given, is called as each file is read with
    its path and the number of bytes read so far. Raises TransferError if the
    resource can't be stored.
    """
    if cfg is not None and not cfg["policy"]["allow_directories"]:
        raise TransferError("the archive doesn't allow directory resources")
    hasher = util.DirectoryHasher()
    if cfg is None:
        for relpath, source in entries:
            _entry_path(relpath)
            if source is not None:
                try:
                    hasher.add_stream(
                        relpath, source, progress=_progress_for(progress, relpath)
                    )
                except ValueError as err:
                    raise TransferError(str(err)) from err
        digest = hasher.hexdigest()
        return Received(None, digest, _check_hash(digest, sha1))
    target = _destination(cfg, id, name)
    partial = target.parent / _partial_name(target.name)
    created = False
    try:
        try:
            partial.mkdir()
        except FileExistsError:
            raise TransferError(
                f"'{partial}' is left over from an earlier transfer; remove it first"
            ) from None
        created = True
        for relpath, source in entries:
            path = partial / _entry_path(relpath)
            if source is None:
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(path, "xb") as fp:
                    file_hash = util.hash_stream(
                        source,
                        copy_to=fp,
                        progress=_progress_for(progress, relpath),
                    )
                    fp.flush()
                    os.fsync(fp.fileno())
                hasher.add(relpath, file_hash)
            except (FileExistsError, ValueError):
                raise TransferError(f"'{relpath}' appears more than once") from None
        digest = hasher.hexdigest()
        verified = _check_hash(digest, sha1)
        pfix = nbank_archive.permission_fixer(cfg)
        # contents first, so read-only directories can still be entered
        for path in sorted(partial.rglob("*"), reverse=True):
            pfix(path)
        pfix(partial)
        _move_into_place(partial, target)
        created = False
        return Received(target, digest, verified)
    except OSError as err:
        raise TransferError(str(err)) from err
    finally:
        if created and partial.exists():
            nbank_archive.remove(partial)


# size of each read from a tar file or tape device
_tape_read_size = 1 << 20


class _BlockReader(io.RawIOBase):
    """Reads from raw in requests of a fixed size, however little the caller asks for.

    A tape drive in variable-block mode fails (with ENOMEM) any read smaller
    than the block on the tape, so each read has to ask for at least a whole
    block. Each request returns at most one block.
    """

    def __init__(self, raw: BinaryIO, size: int):
        self.raw = raw
        self.size = size
        self._buf = memoryview(b"")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        if not self._buf:
            self._buf = memoryview(self.raw.read(self.size) or b"")
        n = min(len(buffer), len(self._buf))
        buffer[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


@contextmanager
def open_tar(path: str | Path) -> Iterator[tarfile.TarFile]:
    """Opens a tar file for reading in order, without seeking, as a context manager.

    path can be a file, a tape device, or '-' for standard input. Members have to
    be read in order: a member's data can't be read after moving on to the next.
    Files and devices are read _tape_read_size bytes at a time, which is enough
    for tapes written with blocks up to that size.
    """
    import sys

    if str(path) == "-":
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as tar:
            yield tar
        return
    with open(path, "rb", buffering=0) as raw:
        is_device = stat.S_ISCHR(os.fstat(raw.fileno()).st_mode)
        try:
            reader = _BlockReader(raw, _tape_read_size)
            with tarfile.open(fileobj=reader, mode="r|*") as tar:
                yield tar
        except OSError as err:
            if is_device and err.errno == errno.ENOMEM:
                raise OSError(
                    err.errno,
                    f"the tape has blocks larger than {_tape_read_size} bytes, "
                    "the largest this can read",
                ) from err
            raise


class TarResource(NamedTuple):
    """A registered resource read from a tar file; see iter_tar_resources."""

    record: dict
    name: str
    data: BinaryIO | Iterator
    is_dir: bool
    size: int | None


def iter_tar_resources(
    tar: tarfile.TarFile, lookup: Callable[[str, tarfile.TarInfo], dict | None]
) -> Iterator[TarResource]:
    """Yields the registered resources in a tar file, reading it in order.

    lookup(id, member) returns the registry record for a resource id, or None if
    it isn't registered. A member is a resource if the stem of its name (without
    any directories or extension) is a registered id, so members can be stored
    under their bare names or under full paths.

    Yields a TarResource (record, name, data, is_dir, size) for each resource,
    where name is the member's base name and size is a file resource's size in
    bytes (None for a directory resource). For a file resource, data is a stream of its contents.
    For a directory resource, data is an iterator of (path, stream) entries, as
    receive_directory takes, made from the members that follow it with its name
    as a prefix; a stream of None is a subdirectory. Each resource's data has to
    be read before asking for the next; data that isn't read is skipped.
    Directories that aren't registered resources, members that aren't files
    or directories, and a manifest.json at the top level are ignored. A directory resource containing links or
    other special members raises TransferError from its entries, after they've
    been read to the end of the resource.
    """
    members = iter(tar)
    pending: list[tarfile.TarInfo] = []

    def next_member() -> tarfile.TarInfo | None:
        if pending:
            return pending.pop()
        return next(members, None)

    def directory_entries(prefix: str) -> Iterator:
        special = []
        while (member := next_member()) is not None:
            if not member.name.startswith(prefix):
                pending.append(member)
                break
            relpath = member.name[len(prefix) :].rstrip("/")
            if member.isdir():
                yield relpath, None
            elif member.isreg():
                yield relpath, tar.extractfile(member)
            else:
                special.append(member.name)
        if special:
            raise TransferError(
                f"contains members that aren't files or directories: {', '.join(special)}"
            )

    while (member := next_member()) is not None:
        path = PurePosixPath(member.name)
        if not (member.isdir() or member.isreg()) or path == PurePosixPath(
            manifest_name
        ):
            continue
        record = lookup(path.stem, member)
        if record is None:
            continue
        if member.isreg():
            yield TarResource(
                record, path.name, tar.extractfile(member), False, member.size
            )
            continue
        entries = directory_entries(member.name.rstrip("/") + "/")
        yield TarResource(record, path.name, entries, True, None)
        # skip whatever the caller didn't read
        try:
            for _ in entries:
                pass
        except TransferError:
            pass


class _HashingReader:
    """Reads a source file of known size, hashing what's read.

    Raises TransferError for read errors and for a file that ends early, so
    they can be told apart from errors writing the export.
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
    """Returns the status of a file or directory to export.

    Raises TransferError if it can't be read or is anything else.
    """
    try:
        st = path.lstat()
    except OSError as err:
        raise TransferError(f"unable to read '{path}': {err.strerror}") from err
    if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
        raise TransferError(f"'{path}' is not a file or directory")
    return st


def _walk(root: Path) -> Iterator[Path]:
    """Yields everything under root in sorted order, each directory before its contents."""
    try:
        children = sorted(root.iterdir())
    except OSError as err:
        raise TransferError(f"unable to read '{root}': {err.strerror}") from err
    for child in children:
        yield child
        if child.is_dir() and not child.is_symlink():
            yield from _walk(child)


class ExportWriter:
    """Writes resources to an export, one at a time.

    For each resource, begin() is called with its name, then add_dir() and
    add_file() for the resource and everything in it, with member names that
    start with the resource's name. Then commit() keeps it, or abort() removes
    every trace of it. Errors writing the export are raised as OSError.
    """

    def begin(self, name: str) -> None:
        pass

    def add_dir(self, name: str, st: os.stat_result) -> None:
        raise NotImplementedError

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
    """Writes to a tar file, which must be open for writing to a regular file
    (mode 'w' or 'x', not 'w|') so a resource that fails can be cut back out."""

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

    def add_dir(self, name: str, st: os.stat_result) -> None:
        info = self._info(name, st)
        info.type = tarfile.DIRTYPE
        self.tar.addfile(info)

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
    """Writes to a zip file, which must be open for writing to a regular file
    (mode 'w' or 'x') so a resource that fails can be cut back out."""

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

    def add_dir(self, name: str, st: os.stat_result) -> None:
        info = self._info(name + "/", st)
        info.external_attr |= 0x10  # MS-DOS directory flag
        self.zf.writestr(info, b"")

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
    """Writes to a directory. Each resource is written under a temporary name
    and moved into place once it's verified, never replacing anything."""

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

    def _path(self, name: str) -> Path:
        return self._partial.joinpath(*PurePosixPath(name).parts[1:])

    def add_dir(self, name: str, st: os.stat_result) -> None:
        self._path(name).mkdir()

    def add_file(self, name: str, st: os.stat_result, reader: _HashingReader) -> None:
        path = self._path(name)
        with open(path, "xb") as fp:
            reader.copy_to(fp)
            fp.flush()
            os.fsync(fp.fileno())
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    def add_bytes(self, name: str, data: bytes) -> None:
        with open(self._path(name), "xb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())

    def commit(self) -> None:
        _move_into_place(self._partial, self._target)
        self._partial = None

    def abort(self) -> None:
        if self._partial is not None and (
            self._partial.exists() or self._partial.is_symlink()
        ):
            nbank_archive.remove(self._partial)
        self._partial = None


@contextmanager
def open_export(path: Path, compress: bool = False) -> Iterator[ExportWriter]:
    """Opens an export for writing, as a tar file, a zip file, or a directory.

    The format comes from the extension of path: '.tar' or '.zip', and anything
    else is a directory, which is created if needed. A tar or zip file must not
    exist already. compress applies to zip files, whose members are otherwise
    stored uncompressed. Raises ValueError for compressed tar extensions and
    other archive formats, which would otherwise be taken as directory names,
    and OSError if the export can't be created.
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
    """Writes a resource to an export, checking its hash as it's read.

    The resource is stored under the name of its file in the archive, and a
    directory resource as that directory and everything in it, which is how
    iter_tar_resources and `nbank archive register-tar` expect to find it in a
    tar file. If the hash doesn't match, or the resource can't be read, the
    writer removes what was written of it, so the export only ever holds
    verified resources. progress is called as each file is read with its path
    (the resource's name, or a path inside a directory resource) and the number
    of bytes read so far.

    Returns True if the hash was checked, and False if the registry has no hash
    to check against. Raises TransferError if the resource can't be read, doesn't
    match, or is already in a directory export, and OSError if the export can't
    be written.
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
        st = _stat(source.path)
        if not stat.S_ISDIR(st.st_mode):
            digest = add_file(name, source.path, st, name)
        else:
            writer.add_dir(name, st)
            hasher = util.DirectoryHasher()
            for path in _walk(source.path):
                relpath = path.relative_to(source.path).as_posix()
                member = f"{name}/{relpath}"
                st = _stat(path)
                if stat.S_ISDIR(st.st_mode):
                    writer.add_dir(member, st)
                else:
                    hasher.add(relpath, add_file(member, path, st, relpath))
            digest = hasher.hexdigest()
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


def _directory_entries(root: Path) -> Iterator[tuple[str, BinaryIO | None]]:
    """Yields (path, stream) for everything in a directory, as receive_directory
    takes. Each file is open only until the next entry is asked for."""
    for path in _walk(root):
        relpath = path.relative_to(root).as_posix()
        if stat.S_ISDIR(_stat(path).st_mode):
            yield relpath, None
        else:
            with open(path, "rb") as fp:
                yield relpath, fp


def receive_source(
    cfg: nbank_archive.ArchiveConfig | None,
    source: Source,
    progress: Progress | None = None,
) -> Received:
    """Stores a resource found by find_sources in a neurobank archive, checking
    its hash. See receive_file and receive_directory, which this calls.

    Errors reading the source raise TransferError, as with any other problem
    with the resource, including in a dry run (cfg None).
    """
    if source.path is None:
        raise TransferError(source.error or "no copy to read")
    name = source.path.name
    try:
        if source.path.is_dir():
            entries = _directory_entries(source.path)
            return receive_directory(
                cfg, source.id, name, entries, source.sha1, progress
            )
        with open(source.path, "rb") as fp:
            return receive_file(cfg, source.id, name, fp, source.sha1, progress)
    except OSError as err:
        raise TransferError(f"unable to read '{source.path}': {err}") from err


# name of the manifest in an export, which tar readers skip
manifest_name = "manifest.json"


def write_manifest(writer: ExportWriter, manifest: dict) -> None:
    """Writes manifest to an export as JSON, under manifest_name.

    Raises TransferError if a directory export already has a manifest, and
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
    """Records a resource that's been stored at path as a location in archive_name.

    If the registry refuses, the stored copy is removed, so the archive won't
    contain a resource the registry doesn't record as being there. If the
    registry can't be reached, the location may or may not have been added, so
    the copy is left for `nbank check archive` to sort out. Raises TransferError
    in both cases.

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
