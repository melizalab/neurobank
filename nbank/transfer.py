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

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

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


def partial_name(name: str) -> str:
    """Returns the name a resource is written under until it's verified."""
    return f".{name}.partial"


def partial_target(name: str) -> str | None:
    """Returns the name a temporary transfer file was going to be given, or None.

    This is the inverse of partial_name, for recognizing files left over from
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


def receive_file(
    cfg: nbank_archive.ArchiveConfig | None,
    id: str,
    name: str,
    source: BinaryIO,
    sha1: str | None,
) -> Received:
    """Stores a file resource read from source in a neurobank archive, checking its hash.

    name is the source's file name, which supplies the extension. The data is
    written to a temporary name beside its destination and hashed as it's
    written. It's moved into place only if the hash matches sha1 (the registered
    hash) or sha1 is None; otherwise, or on any error, the temporary file is
    removed. The archive's permission policy is applied before the move.

    If cfg is None, this is a dry run: the data is hashed and checked, and
    nothing is written. Raises TransferError if the resource can't be stored.
    """
    if cfg is None:
        digest = util.hash_stream(source)
        return Received(None, digest, _check_hash(digest, sha1))
    target = _destination(cfg, id, name)
    partial = target.parent / partial_name(target.name)
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
            digest = util.hash_stream(source, copy_to=fp)
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
    nothing is written. Raises TransferError if the resource can't be stored.
    """
    hasher = util.DirectoryHasher()
    if cfg is None:
        for relpath, source in entries:
            _entry_path(relpath)
            if source is not None:
                try:
                    hasher.add_stream(relpath, source)
                except ValueError as err:
                    raise TransferError(str(err)) from err
        digest = hasher.hexdigest()
        return Received(None, digest, _check_hash(digest, sha1))
    target = _destination(cfg, id, name)
    partial = target.parent / partial_name(target.name)
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
                    file_hash = util.hash_stream(source, copy_to=fp)
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


__all__ = [
    "Received",
    "Source",
    "TransferError",
    "add_location",
    "find_sources",
    "partial_name",
    "partial_target",
    "receive_directory",
    "receive_file",
]
