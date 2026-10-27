# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Integrity checks for archives and the registry."""

import enum
import os
import stat
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path

from httpx import Client

from nbank import archive, readers, registry, transfer, util


class Status(enum.Enum):
    OK = "OK"
    NOT_HASHED = "OK (no hash to verify)"
    HASH_SKIPPED = "OK (hash not checked)"
    UNREADABLE = "FAILED to read"
    HASH_MISMATCH = "FAILED to match hash"
    MISSING_FROM_ARCHIVE = "MISSING from the archive"
    MISSING_FROM_REGISTRY = "MISSING from the registry"
    REGISTERED_ELSEWHERE = "registered, but NOT located in this archive"
    UNVERIFIED_ELSEWHERE = (
        "registered, but NOT located in this archive (no hash to verify contents)"
    )
    CHANGED_ELSEWHERE = "registered, but contents DIFFER from the registered hash"
    MISPLACED = "in the WRONG subdirectory"
    DUPLICATE = "DUPLICATE file for the same resource"
    UNEXPECTED = "UNEXPECTED file outside a resource subdirectory"
    SYMLINK = "SYMBOLIC link"
    DIRECTORY = "a DIRECTORY, which isn't a resource"
    INCOMPLETE = "INCOMPLETE transfer or deposit (safe to delete)"
    WRONG_OWNER = "WRONG owner"
    WRONG_GROUP = "WRONG group"
    WRONG_MODE = "WRONG permissions"
    NO_KEY = "location has NO key"
    WRONG_KEY = "location key DOESN'T MATCH the file name"
    NO_STAGING = "MISSING the staging directory"
    NO_LOCATION = "has NO locations"
    EMPTY_ARCHIVE = "has no resources"
    INVALID_FOR_DTYPE = "NOT a valid file of its dtype"
    READER_FAILED = "metadata reader FAILED to load"


@dataclass(frozen=True)
class Finding:
    """The result of checking one resource, file, or archive.

    resource is the resource's id, or None for layout directories and whole-archive
    findings; path is the file or directory concerned, and detail says more about the
    problem. archive names the archive for whole-archive findings. locations lists the
    registered archives of a resource found in an archive it isn't registered to. fixed
    is True if the problem was fixed. ok is False only for errors.
    """

    status: Status
    resource: str | None
    path: Path | None = None
    detail: str = ""
    fixed: bool = False
    archive: str | None = None
    locations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.fixed or self.status in (
            Status.OK,
            Status.NOT_HASHED,
            Status.HASH_SKIPPED,
            Status.EMPTY_ARCHIVE,
        )


def _resources_without_locations(session: Client, registry_url: str) -> Iterator[str]:
    """Yields the names of resources in the registry that have no locations."""
    url, params = registry.find_resource(registry_url, has_location="false")
    for item in util.query_registry_stream(session, url, params):
        yield item["name"]


def _archive_has_resources(
    session: Client, registry_url: str, archive_name: str
) -> bool:
    """True if the registry has any resources in archive_name. Stops at the first."""
    url, _ = registry.find_resource(registry_url)
    items = util.query_registry_paginated(session, url, {"archive": archive_name})
    return next(items, None) is not None


def check_registry(session: Client, registry_url: str) -> Iterator[Finding]:
    """Checks the registry for resources without locations and empty archives.

    Yields a Finding for each resource with no locations (an error) and each
    archive with no resources.
    """
    for name in _resources_without_locations(session, registry_url):
        yield Finding(Status.NO_LOCATION, name)
    url, params = registry.get_archives(registry_url)
    for item in util.query_registry_paginated(session, url, params):
        if not _archive_has_resources(session, registry_url, item["name"]):
            yield Finding(Status.EMPTY_ARCHIVE, None, archive=item["name"])


def registry_locations_in_archive(
    session: Client, registry_url: str, archive_name: str
) -> dict[str, dict]:
    """Returns {name: location} for the resources the registry places in
    archive_name. Each location has its key, and its resource's filename and sha1."""
    url, params = registry.find_locations(registry_url, archive=archive_name)
    return {
        location["resource_name"]: location
        for location in util.query_registry_stream(session, url, params)
    }


def registry_resources_in_archive(
    session: Client, registry_url: str, archive_name: str
) -> dict[str, str | None]:
    """Returns {name: sha1} for the resources the registry places in archive_name."""
    located = registry_locations_in_archive(session, registry_url, archive_name)
    return {name: location["sha1"] for name, location in located.items()}


def _scan_archive(archive_path: Path) -> tuple[dict[str, list[Path]], list[Finding]]:
    """Returns ({resource name: [files]}, [layout problems]) for an archive."""
    files = defaultdict(list)
    base = archive_path / archive._resource_subdir
    try:
        entries = sorted(base.iterdir())
    except OSError as err:
        detail = f"unable to list directory: {err.strerror}"
        return files, [Finding(Status.UNREADABLE, None, base, detail)]
    problems = []
    for entry in entries:
        if not entry.is_dir():
            problems.append(Finding(Status.UNEXPECTED, entry.name, entry))
            continue
        if entry.is_symlink():
            problems.append(Finding(Status.SYMLINK, None, entry))
        try:
            contents = sorted(entry.iterdir())
        except PermissionError:
            problems.append(
                Finding(Status.UNREADABLE, None, entry, "unable to list directory")
            )
            continue
        for resource_file in contents:
            target = transfer.partial_target(resource_file.name)
            if target is not None:
                problems.append(
                    Finding(
                        Status.INCOMPLETE,
                        Path(target).stem,
                        resource_file,
                        f"last modified {_age(resource_file)} ago",
                    )
                )
                continue
            files[resource_file.stem].append(resource_file)
    staging = archive_path / archive._staging_subdir
    if staging.is_dir():
        for entry in _listdir(staging):
            problems.append(
                Finding(
                    Status.INCOMPLETE,
                    None,
                    entry,
                    f"left in staging; last modified {_age(entry)} ago",
                )
            )
    return files, problems


def _age(path: Path) -> str:
    """Returns how long ago path was last modified, roughly, e.g. '3 days'."""
    import time

    seconds = max(0, time.time() - path.lstat().st_mtime)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return "less than a minute"


class DtypeReaders:
    """Checks files against the metadata readers of their dtypes, loading each reader
    the first time it's needed.

    extensions, {dtype: extension or None}, has every dtype the registry knows. A file
    its reader rejects is tried with the readers of the other dtypes that have its
    extension (ignoring case), which are suggested if they accept it. checked and unchecked count the
    files of each dtype that were, and weren't (no reader), checked.
    """

    def __init__(self, extensions: Mapping[str, str | None]):
        self.extensions = {
            dtype: ext.lstrip(".").lower() for dtype, ext in extensions.items() if ext
        }
        self.checked: Counter[str] = Counter()
        self.unchecked: Counter[str] = Counter()
        self._readers: dict[str, readers.Reader | None] = {}

    def _reader(self, dtype: str) -> tuple[readers.Reader | None, Finding | None]:
        """Returns dtype's reader (or None), and a READER_FAILED finding the first time
        it can't be loaded."""
        if dtype in self._readers:
            return self._readers[dtype], None
        try:
            reader = readers.find_reader(dtype)
        except RuntimeError as err:
            self._readers[dtype] = None
            return None, Finding(Status.READER_FAILED, None, detail=str(err))
        self._readers[dtype] = reader
        return reader, None

    def _accepts(self, dtype: str, path: Path) -> bool:
        reader, _ = self._reader(dtype)
        if reader is None:
            return False
        try:
            readers.read_file_metadata(reader, dtype, path)
        except ValueError:
            return False
        return True

    def check(self, name: str, path: Path, dtype: str) -> Iterator[Finding]:
        """Yields INVALID_FOR_DTYPE if dtype's reader rejects the file at path, and
        READER_FAILED if the reader can't be loaded."""
        reader, failed = self._reader(dtype)
        if failed is not None:
            yield failed
        if reader is None:
            self.unchecked[dtype] += 1
            return
        self.checked[dtype] += 1
        try:
            readers.read_file_metadata(reader, dtype, path)
        except ValueError as err:
            detail = str(err)
        else:
            return
        suffix = path.suffix.lstrip(".").lower()
        others = sorted(
            other
            for other, ext in self.extensions.items()
            if other != dtype and ext == suffix and self._accepts(other, path)
        )
        if others:
            detail += f"; might be {' or '.join(others)}"
        yield Finding(Status.INVALID_FOR_DTYPE, name, path, detail)


def check_archive_contents(
    archive_path: Path,
    expected: Mapping[str, str | None],
    *,
    check_hash: bool = True,
    keys: Mapping[str, str | None] | None = None,
    dtypes: Mapping[str, str] | None = None,
    dtype_readers: DtypeReaders | None = None,
) -> Iterator[Finding]:
    """Compares the files in a local archive with expected, {name: sha1 or None}.

    Yields Findings (see Status) for the expected resources and for the files in the
    archive; a resource can have more than one. check_hash=False skips reading files.
    keys, {name: key or None}, gives the key of each resource's location in the
    archive, which should be the name of its file (NO_KEY, WRONG_KEY); without it,
    keys aren't checked. With dtypes, {name: dtype}, and dtype_readers, files whose
    contents match (or have no hash) are checked with their dtype's reader, unless
    check_hash is False (INVALID_FOR_DTYPE, READER_FAILED). Doesn't contact the
    registry. Raises archive.ArchiveHostError on Windows.
    """
    archive.require_posix("check an archive")
    remaining = dict(expected)
    files, problems = _scan_archive(archive_path)
    yield from problems
    for name, paths in files.items():
        if name not in remaining:
            for path in paths:
                yield Finding(Status.MISSING_FROM_REGISTRY, name, path)
            continue
        sha1 = remaining.pop(name)
        if len(paths) > 1:
            for path in paths:
                yield Finding(Status.DUPLICATE, name, path)
            continue
        path = paths[0]
        if path.is_symlink():
            status = Status.SYMLINK
        elif path.is_dir():
            status = Status.DIRECTORY
        elif path.parent.name != archive.id_stub(name):
            status = Status.MISPLACED
        elif not os.access(path, os.R_OK):
            status = Status.UNREADABLE
        elif sha1 is None:
            status = Status.NOT_HASHED
        elif not check_hash:
            status = Status.HASH_SKIPPED
        elif util.hash(path) != sha1:
            status = Status.HASH_MISMATCH
        else:
            status = Status.OK
        yield Finding(status, name, path)
        if (
            check_hash
            and dtypes is not None
            and dtype_readers is not None
            and name in dtypes
            and status in (Status.OK, Status.NOT_HASHED)
        ):
            yield from dtype_readers.check(name, path, dtypes[name])
        # a misplaced file, a link or a directory isn't where a key could point
        if keys is None or status in (
            Status.SYMLINK,
            Status.DIRECTORY,
            Status.MISPLACED,
        ):
            continue
        key = keys.get(name)
        if key is None:
            yield Finding(Status.NO_KEY, name, path)
        elif key != path.name:
            yield Finding(Status.WRONG_KEY, name, path, f"the key is '{key}'")
    for name in remaining:
        yield Finding(Status.MISSING_FROM_ARCHIVE, name)


def recheck_unregistered(
    session: Client, registry_url: str, findings: Iterable[Finding]
) -> Iterator[Finding]:
    """Looks up the resources of MISSING_FROM_REGISTRY findings in the registry.

    A file whose resource is registered to other archives is yielded as
    REGISTERED_ELSEWHERE if its hash matches, CHANGED_ELSEWHERE if not, or
    UNVERIFIED_ELSEWHERE if the registry has no hash, with locations set. Other findings
    are yielded unchanged.
    """
    findings = list(findings)
    names = sorted(
        {
            f.resource
            for f in findings
            if f.status == Status.MISSING_FROM_REGISTRY and f.resource is not None
        }
    )
    records = {}
    for i in range(0, len(names), util.bulk_batch_size):
        url, query = registry.get_resource_bulk(
            registry_url, names[i : i + util.bulk_batch_size]
        )
        for record in util.query_registry_bulk(session, url, query):
            records[record["name"]] = record
    for finding in findings:
        record = records.get(finding.resource)
        if finding.status != Status.MISSING_FROM_REGISTRY or record is None:
            yield finding
            continue
        locations = tuple(record["locations"])
        detail = (
            f"located in: {', '.join(locations)}"
            if locations
            else "the registry lists no locations"
        )
        sha1 = record["sha1"]
        path = finding.path
        assert path is not None  # MISSING_FROM_REGISTRY findings are files
        if path.is_dir():
            status = Status.DIRECTORY
        elif not os.access(path, os.R_OK):
            status = Status.UNREADABLE
        elif sha1 is None:
            status = Status.UNVERIFIED_ELSEWHERE
        elif util.hash(path) != sha1:
            status = Status.CHANGED_ELSEWHERE
        else:
            status = Status.REGISTERED_ELSEWHERE
        yield replace(finding, status=status, detail=detail, locations=locations)


def _user_name(uid: int) -> str:
    import pwd

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
    import grp

    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _check_path(
    cfg: archive.ArchiveConfig,
    path: Path,
    resource: str | None,
    uid: int | None,
    gid: int,
) -> Iterator[Finding]:
    """Checks the owner (unless uid is None), group, and mode of path."""
    st = path.lstat()
    if uid is not None and st.st_uid != uid:
        detail = f"owner is {_user_name(st.st_uid)}, expected {_user_name(uid)}"
        yield Finding(Status.WRONG_OWNER, resource, path, detail)
    if st.st_gid != gid:
        detail = f"group is {_group_name(st.st_gid)}, expected {_group_name(gid)}"
        yield Finding(Status.WRONG_GROUP, resource, path, detail)
    mode = stat.S_IMODE(st.st_mode)
    required, forbidden = archive.mode_policy(cfg, path)
    missing = required & ~mode
    extra = mode & forbidden
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"missing {missing:04o}")
        if extra:
            problems.append(f"has {extra:04o}, which the policy forbids")
        detail = f"mode is {mode:04o}: {', '.join(problems)}"
        yield Finding(Status.WRONG_MODE, resource, path, detail)


def _listdir(path: Path) -> list[Path]:
    """Returns the sorted contents of path, or [] if it can't be listed."""
    try:
        return sorted(path.iterdir())
    except PermissionError:
        return []


def _archive_tree(base: Path) -> Iterator[tuple[Path, str | None]]:
    """Yields (path, resource name) for the resources directory and everything in it.

    The name is None for the layout directories. Each directory comes before its
    contents. Symbolic links, stray files, and leftover partial transfers are skipped.
    """
    yield base, None
    for stub_dir in _listdir(base):
        if not stub_dir.is_dir() or stub_dir.is_symlink():
            continue
        yield stub_dir, None
        for resource_path in _listdir(stub_dir):
            if resource_path.is_symlink():
                continue
            if transfer.partial_target(resource_path.name) is not None:
                continue
            yield resource_path, resource_path.stem


def check_archive_permissions(
    cfg: archive.ArchiveConfig, *, fix: bool = False
) -> Iterator[Finding]:
    """Checks ownership and modes under resources/, and of the staging directory,
    against the archive's policy. A missing staging directory is a finding, which fix
    creates.

    Owners must be the policy's user (unless it's null) and group, and modes follow
    archive.mode_policy. Symbolic links, stray files, leftover partial transfers, and
    directories that can't be listed are skipped; check_archive_contents reports them.
    With fix, fixes what it can (ownership needs root) and marks those findings fixed.
    Raises ValueError if the policy's user or group doesn't exist, and
    archive.ArchiveHostError on Windows.
    """
    archive.require_posix("check an archive")
    import grp
    import pwd

    access = cfg.policy.access
    user = access.user
    try:
        uid = None if user is None else pwd.getpwnam(user).pw_uid
    except KeyError as err:
        raise ValueError(f"archive user '{user}' does not exist") from err
    try:
        gid = grp.getgrnam(access.group).gr_gid
    except KeyError as err:
        raise ValueError(f"archive group '{access.group}' does not exist") from err
    base = cfg.path / archive._resource_subdir
    if not base.exists():
        return  # reported by check_archive_contents
    pfix = archive.permission_fixer(cfg, quiet=True) if fix else None
    staging = archive.staging_dir(cfg)
    if not staging.is_dir():
        finding = Finding(
            Status.NO_STAGING, None, staging, "needed to deposit directories as zips"
        )
        if pfix is not None:
            try:
                staging.mkdir()
            except OSError as err:
                finding = replace(finding, detail=f"unable to create it: {err}")
            else:
                pfix(staging)
                finding = replace(finding, fixed=True)
        yield finding
    # lazily, so a directory fixed on the way can then be listed
    paths = _archive_tree(base)
    if staging.is_dir():
        paths = chain(paths, [(staging, None)])
    for path, name in paths:
        findings = list(_check_path(cfg, path, name, uid, gid))
        if findings and pfix is not None:
            pfix(path)
            remaining = {f.status for f in _check_path(cfg, path, name, uid, gid)}
            findings = [replace(f, fixed=f.status not in remaining) for f in findings]
        yield from findings


__all__ = [
    "DtypeReaders",
    "Finding",
    "Status",
    "check_archive_contents",
    "check_archive_permissions",
    "check_registry",
    "registry_resources_in_archive",
]
