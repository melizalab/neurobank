# -*- mode: python -*-
"""Integrity checks for archives and the registry.

Copyright (C) 2026 Dan Meliza <dan@meliza.org>
"""

import enum
import grp
import os
import pwd
import stat
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from httpx import Client

from nbank import archive, registry, transfer, util


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
    INCOMPLETE = "INCOMPLETE transfer (safe to delete)"
    WRONG_OWNER = "WRONG owner"
    WRONG_GROUP = "WRONG group"
    WRONG_MODE = "WRONG permissions"
    NO_LOCATION = "has NO locations"
    EMPTY_ARCHIVE = "has no resources"


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
    for item in util.query_registry_paginated(session, url, params):
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


def registry_resources_in_archive(
    session: Client, registry_url: str, archive_name: str
) -> dict[str, str | None]:
    """Returns {name: sha1} for the resources the registry places in archive_name."""
    url, _ = registry.find_resource(registry_url)
    return {
        item["name"]: item["sha1"]
        for item in util.query_registry_paginated(
            session, url, {"archive": archive_name}
        )
    }


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


def _symlinks_in(path: Path) -> Iterator[Path]:
    """Returns the symbolic links inside the directory at path, in sorted order."""
    return (p for p in sorted(path.rglob("*")) if p.is_symlink())


def check_archive_contents(
    archive_path: Path, expected: Mapping[str, str | None], check_hash: bool = True
) -> Iterator[Finding]:
    """Compares the files in a local archive with expected, {name: sha1 or None}.

    Yields Findings (see Status) for the expected resources and for the files in the
    archive; a resource can have more than one. check_hash=False skips reading files.
    Doesn't contact the registry.
    """
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
        if status != Status.SYMLINK and path.is_dir():
            for link in _symlinks_in(path):
                yield Finding(Status.SYMLINK, name, link)
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
        {f.resource for f in findings if f.status == Status.MISSING_FROM_REGISTRY}
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
        if not os.access(finding.path, os.R_OK):
            status = Status.UNREADABLE
        elif sha1 is None:
            status = Status.UNVERIFIED_ELSEWHERE
        elif util.hash(finding.path) != sha1:
            status = Status.CHANGED_ELSEWHERE
        else:
            status = Status.REGISTERED_ELSEWHERE
        yield replace(finding, status=status, detail=detail, locations=locations)


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
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


def _resource_tree(path: Path, name: str) -> Iterator[tuple[Path, str]]:
    """Yields (path, name) for a resource and everything in it.

    Each directory comes before its contents. Symbolic links are skipped.
    """
    yield path, name
    if path.is_dir():
        for child in _listdir(path):
            if not child.is_symlink():
                yield from _resource_tree(child, name)


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
            yield from _resource_tree(resource_path, resource_path.stem)


def check_archive_permissions(
    cfg: archive.ArchiveConfig, fix: bool = False
) -> Iterator[Finding]:
    """Checks ownership and modes under resources/ against the archive's policy.

    Owners must be the policy's user (unless it's null) and group, and modes follow
    archive.mode_policy. Symbolic links, stray files, leftover partial transfers, and
    directories that can't be listed are skipped; check_archive_contents reports them.
    With fix, fixes what it can (ownership needs root) and marks those findings fixed.
    Raises ValueError if the policy's user or group doesn't exist.
    """
    access = cfg["policy"]["access"]
    user = access.get("user")
    try:
        uid = None if user is None else pwd.getpwnam(user).pw_uid
    except KeyError as err:
        raise ValueError(f"archive user '{user}' does not exist") from err
    try:
        gid = grp.getgrnam(access["group"]).gr_gid
    except KeyError as err:
        raise ValueError(f"archive group '{access['group']}' does not exist") from err
    base = cfg["path"] / archive._resource_subdir
    if not base.exists():
        return  # reported by check_archive_contents
    pfix = archive.permission_fixer(cfg, quiet=True) if fix else None
    for path, name in _archive_tree(base):
        findings = list(_check_path(cfg, path, name, uid, gid))
        if findings and pfix is not None:
            pfix(path)
            remaining = {f.status for f in _check_path(cfg, path, name, uid, gid)}
            findings = [replace(f, fixed=f.status not in remaining) for f in findings]
        yield from findings


__all__ = [
    "Finding",
    "Status",
    "check_archive_contents",
    "check_archive_permissions",
    "check_registry",
    "registry_resources_in_archive",
]
