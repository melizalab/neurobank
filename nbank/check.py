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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from httpx import Client

from nbank import archive, registry, util


class Status(enum.Enum):
    OK = "OK"
    NOT_HASHED = "OK (no hash to verify)"
    UNREADABLE = "FAILED to read"
    HASH_MISMATCH = "FAILED to match hash"
    MISSING_FROM_ARCHIVE = "MISSING from the archive"
    MISSING_FROM_REGISTRY = "MISSING from the registry"
    MISPLACED = "in the WRONG subdirectory"
    DUPLICATE = "DUPLICATE file for the same resource"
    UNEXPECTED = "UNEXPECTED file outside a resource subdirectory"
    SYMLINK = "SYMBOLIC link"
    WRONG_OWNER = "WRONG owner"
    WRONG_GROUP = "WRONG group"
    WRONG_MODE = "WRONG permissions"


@dataclass(frozen=True)
class Finding:
    """The result of checking one resource or file.

    resource is None for directories that belong to the archive layout rather
    than to a resource. fixed is True if the problem was found and then fixed.
    """

    status: Status
    resource: str | None
    path: Path | None = None
    detail: str = ""
    fixed: bool = False

    @property
    def ok(self) -> bool:
        return self.fixed or self.status in (Status.OK, Status.NOT_HASHED)


def registry_resources_in_archive(
    session: Client, registry_url: str, archive_name: str
) -> dict[str, str | None]:
    """Returns {name: sha1} for the resources the registry places in archive_name."""
    url, _ = registry.find_resource(registry_url)
    # the registry's location filter is a substring match, so filter again here
    # TODO: remove this if the registry is patched to support exact matches
    return {
        item["name"]: item["sha1"]
        for item in util.query_registry_paginated(
            session, url, {"location": archive_name}
        )
        if archive_name in item["locations"]
    }


def _scan_archive(archive_path: Path) -> tuple[dict[str, list[Path]], list[Finding]]:
    """Returns ({resource name: [files]}, [layout problems]) for an archive."""
    files = defaultdict(list)
    problems = []
    for entry in sorted((archive_path / archive._resource_subdir).iterdir()):
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
            files[resource_file.stem].append(resource_file)
    return files, problems


def _symlinks_in(path: Path) -> Iterator[Path]:
    """Returns the symbolic links inside the directory at path, in sorted order."""
    return (p for p in sorted(path.rglob("*")) if p.is_symlink())


def check_archive_contents(
    archive_path: Path, expected: Mapping[str, str | None]
) -> Iterator[Finding]:
    """Compares the files in a local archive against expected resources.

    expected maps resource names to sha1 hashes (or None if the registry has no
    hash). Yields one Finding for each expected resource, indicating whether
    everything is okay or if there is a problem (see Status enum). May yield
    more than one Finding per resource in cases of multiple errors. Doesn't
    contact the registry.

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
    cfg: archive.ArchiveConfig, path: Path, resource: str | None, uid: int, gid: int
) -> Iterator[Finding]:
    """Checks the owner, group, and mode of path."""
    st = path.lstat()
    if st.st_uid != uid:
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
            problems.append(f"has {extra:04o} forbidden by umask")
        detail = f"mode is {mode:04o}: {', '.join(problems)}"
        yield Finding(Status.WRONG_MODE, resource, path, detail)


def _listdir(path: Path) -> list[Path]:
    """Returns the sorted contents of path, or [] if it can't be listed."""
    try:
        return sorted(path.iterdir())
    except PermissionError:
        return []


def _resource_tree(path: Path, name: str) -> Iterator[tuple[Path, str]]:
    """Yields (path, name) for a resource and, for a directory, everything in it.

    Each directory is listed only after it has been yielded, so a caller can
    fix its permissions first. Symbolic links are skipped.
    """
    yield path, name
    if path.is_dir():
        for child in _listdir(path):
            if not child.is_symlink():
                yield from _resource_tree(child, name)


def _archive_tree(base: Path) -> Iterator[tuple[Path, str | None]]:
    """Yields (path, resource name) for the resources directory and everything in it.

    The resource name is None for the resources directory and its
    subdirectories. Directories are listed only after they have been yielded.
    Symbolic links and files directly under base are skipped.
    """
    yield base, None
    for stub_dir in _listdir(base):
        if not stub_dir.is_dir() or stub_dir.is_symlink():
            continue
        yield stub_dir, None
        for resource_path in _listdir(stub_dir):
            if not resource_path.is_symlink():
                yield from _resource_tree(resource_path, resource_path.stem)


def check_archive_permissions(
    cfg: archive.ArchiveConfig, fix: bool = False
) -> Iterator[Finding]:
    """Checks ownership and permissions in a local archive against its policy.

    Every file and directory under resources/ must be owned by the policy's
    user and group and have the mode bits given by archive.mode_policy.
    Symbolic links and files directly under resources/ are skipped, because
    check_archive_contents reports them. Directories that can't be listed are
    skipped too.

    If fix is True, tries to fix each problem with archive.permission_fixer
    and sets fixed on the findings that it resolved. A directory is fixed
    before it's listed, so the fix can make its contents reachable. Changing
    ownership requires running as root.

    Raises ValueError if the policy's user or group doesn't exist on this host.
    """
    access = cfg["policy"]["access"]
    try:
        uid = pwd.getpwnam(access["user"]).pw_uid
    except KeyError as err:
        raise ValueError(f"archive user '{access['user']}' does not exist") from err
    try:
        gid = grp.getgrnam(access["group"]).gr_gid
    except KeyError as err:
        raise ValueError(f"archive group '{access['group']}' does not exist") from err
    pfix = archive.permission_fixer(cfg, quiet=True) if fix else None
    for path, name in _archive_tree(cfg["path"] / archive._resource_subdir):
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
    "registry_resources_in_archive",
]
