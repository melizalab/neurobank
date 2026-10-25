# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Functions for managing a data archive on the local filesystem: creating new archives,
defining how resources are stored in stubbed subdirectories, storing resources, and
managing their permissions.

"""

import json
import logging
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nbank.types import LocationContext, location_scheme

log = logging.getLogger("nbank")  # root logger


@dataclass(frozen=True)
class Access:
    """Who owns an archive's files, and who can read and write them."""

    group: str
    umask: int
    # None for a shared archive, where users deposit under their own accounts
    user: str | None = None
    read_only_resources: bool = True


@dataclass(frozen=True)
class Policy:
    """How an archive's resources are named, hashed and protected."""

    access: Access
    auto_identifiers: bool = False
    auto_id_type: str = "base36"
    require_hash: bool = True


@dataclass(frozen=True)
class ArchiveConfig:
    """An archive's configuration, read from its nbank.json. path is the archive's
    resolved directory, which the file doesn't record."""

    path: Path
    registry: str
    policy: Policy

    @classmethod
    def load(cls, path: Path) -> "ArchiveConfig":
        """Returns the configuration of the archive at path, from its nbank.json.

        Raises FileNotFoundError if there's no nbank.json, and ValueError if it isn't
        valid. Keys that nothing reads (project, owner, retired policies) are ignored.
        """
        fname = path / _config_fname
        with open(fname) as fp:
            try:
                doc = json.load(fp)
                return _parse_config(doc, path.resolve(strict=True))
            except ValueError as err:
                raise ValueError(f"{fname}: {err}") from None


class ArchiveHostError(RuntimeError):
    """An archive was to be written or checked on a host that can't (Windows)."""


def require_posix(action: str) -> None:
    """Raises ArchiveHostError, saying what couldn't be done, unless this is a POSIX
    host."""
    if not _posix:
        raise ArchiveHostError(
            f"unable to {action}: archives are written and checked on a POSIX host "
            "(Linux or macOS), not on Windows"
        )


_README_fname = "README.md"
_config_fname = "nbank.json"
_config_schema = "https://melizalab.github.io/neurobank/config.json#"
_resource_subdir = "resources"
# where files are built before they're stored (e.g. zips of deposited directories)
_staging_subdir = "staging"
_default_umask = 0o002
# Linux needs setgid on directories for new files to inherit their group. BSD
# and macOS always inherit the group of the parent directory.
_setgid = stat.S_ISGID if sys.platform.startswith("linux") else 0
# archives rely on POSIX ownership, groups and mode bits, so they're only written and
# checked on POSIX hosts; Windows hosts can read them
_posix = os.name == "posix"
_README = """
This directory contains a [neurobank](https://github.com/melizalab/neurobank)
data management archive. The following files and directories are part of the archive:

 + README.md: this file
 + nbank.json: information and configuration for the archive
 + resources/:  registered source files and deposited data
 + staging/:  files being prepared for deposit (anything left here is safe to delete)

Files in `resources` are organized into subdirectories based on the first two
characters of the files' identifiers.

For more information, consult the neurobank website at
https://github.com/melizalab/neurobank

# Archive contents

Add notes about the contents of the data archive here. You should also edit
`nbank.json` to set information and policy for your project.

# Quick reference

Deposit resources: `nbank deposit archive_path file-1 [file-2 [file-3]]`

Deposited files are given the group and permissions specified in `nbank.json`.
To find and fix files with the wrong ownership or permissions, run
`nbank check archive --fix archive_path` (as root to fix ownership).

"""


def _section(doc: dict, key: str, where: str) -> dict:
    value = doc.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{where}{key} must be an object")
    return value


def _value(doc: dict, key: str, kind: type, where: str, cls: type) -> Any:
    """Returns doc[key] for the field key of the dataclass cls. A missing or null value
    takes the field's default; ValueError if it has none, or if the value isn't a kind
    (a bool isn't an int)."""
    value = doc.get(key)
    if value is None:
        # a field with a default is a class attribute
        if not hasattr(cls, key):
            raise ValueError(f"{where}{key} is missing")
        return getattr(cls, key)
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise ValueError(f"{where}{key} must be a {kind.__name__}")
    return value


def _parse_config(doc: Any, path: Path) -> ArchiveConfig:
    """Returns the ArchiveConfig for an archive at path from its parsed nbank.json."""
    if not isinstance(doc, dict):
        raise ValueError("must be a JSON object")
    policy = _section(doc, "policy", "")
    access = _section(policy, "access", "policy.")
    umask = access.get("umask")
    # written by hand as an octal string
    if isinstance(umask, str):
        try:
            umask = int(umask, 8)
        except ValueError:
            raise ValueError("policy.access.umask must be an octal number") from None
    if not isinstance(umask, int) or isinstance(umask, bool):
        raise ValueError("policy.access.umask must be an octal number")
    if policy.get("keep_extensions") is False:
        log.warning(
            "%s: the keep_extensions policy is no longer supported; files keep "
            "their extensions",
            path / _config_fname,
        )
    pw, aw = "policy.", "policy.access."
    return ArchiveConfig(
        path=path,
        registry=_value(doc, "registry", str, "", ArchiveConfig),
        policy=Policy(
            access=Access(
                group=_value(access, "group", str, aw, Access),
                umask=umask,
                user=_value(access, "user", str, aw, Access),
                read_only_resources=_value(
                    access, "read_only_resources", bool, aw, Access
                ),
            ),
            auto_identifiers=_value(policy, "auto_identifiers", bool, pw, Policy),
            auto_id_type=_value(policy, "auto_id_type", str, pw, Policy),
            require_hash=_value(policy, "require_hash", bool, pw, Policy),
        ),
    )


_created_files = (_config_fname, _README_fname, ".gitignore")


def verify_can_create(archive_path: Path) -> None:
    """Raises FileExistsError if creating an archive would overwrite existing files."""
    for name in _created_files:
        path = archive_path / name
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"'{path}' already exists")


def create(
    archive_path: Path,
    registry_url: str,
    *,
    umask: int = _default_umask,
    shared: bool = False,
    group: str | None = None,
    read_only_resources: bool = True,
    **policies: Any,
) -> ArchiveConfig:
    """Initializes a new data archive in archive_path and returns its config.

    registry_url is the registry the archive's resources are registered with. umask
    (e.g. 0o027) is recorded in the policy and limits the permissions of everything in
    the archive. With shared, no owner is recorded, for archives where users deposit
    under their own accounts; otherwise the current user is the owner. group defaults to
    the current user's primary group. read_only_resources makes deposited resources
    read-only. policies can set auto_identifiers, auto_id_type, or require_hash.

    Creates archive_path and its parents as needed. Raises FileExistsError if any file
    it would create already exists, ValueError if group doesn't exist,
    ArchiveHostError on Windows, and OSError for other failures.
    """
    require_posix("create an archive")
    import grp
    import pwd
    from os import chown, getgid, getuid

    if group is None:
        group = grp.getgrgid(getgid()).gr_name
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError as err:
        raise ValueError(f"group '{group}' does not exist") from err
    user = None if shared else pwd.getpwuid(getuid()).pw_name

    archive_path = archive_path.resolve(strict=False)
    verify_can_create(archive_path)
    umask &= 0o777  # mask out the umask

    resdir = archive_path / _resource_subdir
    resdir.mkdir(parents=True, exist_ok=True)
    staging = archive_path / _staging_subdir
    staging.mkdir(exist_ok=True)

    fname = archive_path / _README_fname
    fname.write_text(_README)
    fname.chmod(0o666 & ~umask)

    config = {
        "$schema": _config_schema,
        "project": {"name": None, "description": None},
        "owner": {"name": None, "email": None},
        "registry": registry_url,
        "policy": {
            "auto_identifiers": False,
            "auto_id_type": None,
            "require_hash": True,
            "access": {
                "user": user,
                "group": group,
                "umask": umask,
                "read_only_resources": read_only_resources,
            },
        },
    }
    for k, v in policies.items():
        config["policy"][k] = v
    fname = archive_path / _config_fname
    fname.write_text(json.dumps(config, indent=4))
    fname.chmod(0o666 & ~umask)

    fname = archive_path / ".gitignore"
    fname.write_text("resources/\nstaging/\n")
    fname.chmod(0o666 & ~umask)

    cfg = ArchiveConfig.load(archive_path)
    permission_fixer(cfg)(resdir)
    permission_fixer(cfg)(staging)
    for name in _created_files:
        try:
            chown(archive_path / name, -1, gid)
        except PermissionError:
            log.warning("unable to change the group of %s", archive_path / name)
    return cfg


def staging_dir(cfg: ArchiveConfig) -> Path:
    """Returns the directory where files are built before they're stored."""
    return cfg.path / _staging_subdir


def id_stub(id: str) -> str:
    """Returns a short version of id, used for sorting objects into subdirectories."""
    return id[:2]


def resource_path(
    cfg: ArchiveConfig | Path | str, name: str, resolve_ext: bool = False
) -> Path:
    """Returns the path in the archive for name, a resource id or a stored file name.

    With resolve_ext, returns the file actually stored for the resource, whatever its
    extension, or raises FileNotFoundError if there isn't one. To name a new file, use
    new_resource_path.
    """
    root = cfg.path if isinstance(cfg, ArchiveConfig) else Path(cfg)
    partial = root / _resource_subdir / id_stub(name) / name
    if not resolve_ext:
        return partial
    else:
        return resolve_extension(partial)


def new_resource_path(cfg: ArchiveConfig, id: str, source_name: str) -> Path:
    """Returns the path to store a new resource at: the id, plus the extension of
    source_name. Doesn't check whether the resource is already stored."""
    return resource_path(cfg, Path(id).stem + Path(source_name).suffix)


def resolve_extension(path: Path) -> Path:
    """Returns the file stored for path, which may have an extension that path lacks.

    Raises FileNotFoundError if there's no such file.
    """
    if path.exists():
        return path
    paths = path.parent.glob(f"{path.name}.*")
    try:
        return next(paths)
    except StopIteration as err:
        raise FileNotFoundError(f"resource '{path}' does not exist") from err


@location_scheme
class Resource:
    """A resource stored in a local neurobank archive.

    The location's root is the path of the archive, and its key is the name of the
    file, which is in the subdirectory for the id. alt_base replaces the directory that
    contains the archive, e.g. alt_base='/scratch' maps '/home/data/starlings' to
    '/scratch/starlings', for copies of archives on other hosts.

    Raises FileNotFoundError if the file doesn't exist.
    """

    schemes = ("neurobank",)
    local = True

    def __init__(self, root: str, id: str, key: str, alt_base: Path | None = None):
        root = Path(root)
        if alt_base is not None:
            root = Path(alt_base) / root.name
        self.id = id
        # a key is a file name, never a path
        if not key or Path(key).name != key:
            raise FileNotFoundError(f"'{key}' isn't the name of a file")
        self.path = root / _resource_subdir / id_stub(id) / key
        if not self.path.exists():
            raise FileNotFoundError(f"resource '{self.path}' does not exist")

    @classmethod
    def from_location(cls, location, context: LocationContext) -> "Resource | None":
        """Returns None if the location has no key, or the archive or the resource
        isn't on this host."""
        key = location.get("key")
        if key is None:
            log.debug(
                "%s: the location in %s has no key",
                location["resource_name"],
                location.get("archive_name"),
            )
            return None
        try:
            return cls(
                location["root"], location["resource_name"], key, context.alt_base
            )
        except FileNotFoundError:
            return None

    def __str__(self):
        return str(self.path)

    def __repr__(self):
        return f"<local resource: {self.id} @ {self.path}>"

    @property
    def deletable(self) -> bool:
        return _can_remove(self.path)

    def fetch(self, target: Path) -> Path:
        if target.is_dir():
            target = target / self.path.name
        shutil.copyfile(self.path, target)
        return target

    def link(self, target_dir: Path) -> Path:
        linkpath = target_dir / self.path.name
        linkpath.symlink_to(self.path)
        return linkpath

    def unlink(self) -> None:
        remove(self.path)


def _directories_in(path: Path) -> list[Path]:
    """Returns path and every directory inside it, not following symbolic links."""
    return [path] + [p for p in path.rglob("*") if p.is_dir() and not p.is_symlink()]


def _can_remove(path: Path) -> bool:
    """True if this process can remove the resource at path.

    For a directory (not a resource, but one may be left in an archive), this includes
    making read-only directories inside it writable. Always False on Windows, where
    archives aren't written.
    """
    if not _posix or not os.access(path.parent, os.W_OK | os.X_OK):
        return False
    if not path.is_dir() or path.is_symlink():
        return True
    uid = os.getuid()
    return all(
        uid == 0 or d.stat().st_uid == uid or os.access(d, os.W_OK | os.X_OK)
        for d in _directories_in(path)
    )


def remove(path: Path) -> None:
    """Removes the resource at path.

    A directory, which may be left in an archive, is removed with everything in it,
    making read-only directories inside it writable first. Raises PermissionError if
    that isn't allowed, and ArchiveHostError on Windows.
    """
    require_posix("remove a file from an archive")
    if not path.is_dir() or path.is_symlink():
        path.unlink()
        return
    for d in _directories_in(path):
        if not os.access(d, os.W_OK | os.X_OK):
            d.chmod(stat.S_IMODE(d.stat().st_mode) | stat.S_IRWXU)
    shutil.rmtree(path)


def verify_permissions(cfg: ArchiveConfig, src: Path, id: str | None = None) -> None:
    """Raises PermissionError, naming the path and problem, if src can't be deposited.

    src must be readable, and the resources directory (and id's subdirectory, if it
    exists) readable, writable, and searchable. For a directory, which is deposited as
    a zip of its contents, everything inside it must be readable too. Raises
    FileExistsError instead if a file is already stored for the id, whatever its
    extension.
    """
    if not os.access(src, os.R_OK):
        raise PermissionError(f"'{src}' is not readable")
    reqd_perms = os.R_OK | os.W_OK | os.X_OK
    if id is None:
        id = src.name
    tgt_base: Path = cfg.path / _resource_subdir
    tgt_dir = tgt_base / id_stub(id)
    if not os.access(tgt_base, os.F_OK):
        raise PermissionError(f"archive resource directory '{tgt_base}' does not exist")
    if not os.access(tgt_base, reqd_perms):
        raise PermissionError(
            f"insufficient permissions on archive resource directory '{tgt_base}'"
        )
    if os.access(tgt_dir, os.F_OK) and not os.access(tgt_dir, reqd_perms):
        raise PermissionError(
            f"insufficient permissions on archive subdirectory '{tgt_dir}'"
        )
    # check for any file already stored for this resource, whatever its extension
    try:
        existing = resource_path(cfg, Path(id).stem, resolve_ext=True)
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(f"'{existing}' is already stored for this resource")

    if src.is_dir():
        for item in src.rglob("*"):
            perm = (os.R_OK | os.X_OK) if item.is_dir() else os.R_OK
            if not os.access(item, perm):
                raise PermissionError(f"'{item}' is not readable")


def verify_no_symlinks(src: Path) -> None:
    """Raises ValueError, naming the link, if src is or contains a symbolic link (a
    directory can't, as it's deposited as a zip of its contents)."""
    if src.is_symlink():
        raise ValueError(f"'{src}' is a symbolic link")
    if src.is_dir():
        for path in src.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"'{path}' is a symbolic link")


def store_resource(cfg: ArchiveConfig, src: Path, *, id: str | None = None) -> Path:
    """Moves src into the archive as id and returns the stored path.

    id defaults to the name of src. Doesn't register anything: the caller makes sure id
    is valid and registered. The stored name keeps the extension of src. If the
    directory holding src isn't writable, src is copied instead
    and left in place, with a warning. Policies come from cfg, not from the archive's
    nbank.json.

    Raises KeyError if a file is already stored for id, TypeError if src is a
    directory, and ArchiveHostError on Windows.
    """
    require_posix("store a resource in an archive")
    if src.is_dir():
        raise TypeError(f"'{src}' is a directory; zip it first")

    if id is None:
        id = src.name

    # check for any file already stored for this resource, whatever its extension
    try:
        existing = resource_path(cfg, Path(id).stem, resolve_ext=True)
    except FileNotFoundError:
        pass
    else:
        raise KeyError(f"'{existing}' is already stored for this resource")
    tgt_file = new_resource_path(cfg, id, src.name)
    log.debug("%s -> %s", src, tgt_file.name)

    # the source isn't touched until its destination directory exists
    pfix = permission_fixer(cfg)
    tgt_dir = tgt_file.parent
    try:
        tgt_dir.mkdir(parents=True)
        pfix(tgt_dir)
    except FileExistsError:
        pass

    if os.access(src.parent, os.W_OK | os.X_OK):
        shutil.move(src, tgt_file)
    else:
        log.warning(
            "'%s' is not writable; copying '%s' instead of moving it "
            "(source will not be removed)",
            src.parent,
            src,
        )
        shutil.copy2(src, tgt_file)
    pfix(tgt_file)

    return tgt_file


def mode_policy(cfg: ArchiveConfig, path: Path) -> tuple[int, int]:
    """Returns (required, forbidden) mode bits for path under the archive's policy.

    Nothing may have bits the umask forbids. The resources directory and its
    subdirectories need every bit the umask allows, plus setgid on Linux. Other
    directories must be readable and searchable, and files readable, by everyone the
    umask allows. With read_only_resources, resources must not be writable.
    """
    access = cfg.policy.access
    umask = access.umask
    base = cfg.path / _resource_subdir
    try:
        in_resource = len(path.relative_to(base).parts) >= 2
    except ValueError:
        in_resource = False
    forbidden = umask
    if in_resource and access.read_only_resources:
        forbidden |= 0o222
    if path.is_dir():
        if not in_resource:
            return (0o777 & ~umask) | _setgid, umask
        return 0o555 & ~umask, forbidden
    return 0o444 & ~umask, forbidden


def permission_fixer(cfg: ArchiveConfig, quiet: bool = False):
    """Returns a function that sets the ownership and mode of a path in the archive.

    The group comes from the policy, and so does the user if there is one and this is
    running as root; the mode comes from mode_policy. Failures are logged as warnings
    unless quiet is True. Symbolic links are left alone.
    """
    import grp
    import pwd
    from os import chown, getuid

    user = cfg.policy.access.user
    if user is not None and getuid() == 0:
        uid = pwd.getpwnam(user).pw_uid
    else:
        uid = -1
    gid = grp.getgrnam(cfg.policy.access.group).gr_gid

    def fix(p: Path) -> None:
        if p.is_symlink():
            return
        try:
            chown(p, uid, gid)
        except PermissionError:
            if not quiet:
                log.warning("unable to change uid/gid of %s", p)
        required, forbidden = mode_policy(cfg, p)
        try:
            p.chmod((stat.S_IMODE(p.stat().st_mode) | required) & ~forbidden)
        except PermissionError:
            if not quiet:
                log.warning("unable to change permissions of %s", p)

    return fix


__all__ = [
    "Access",
    "ArchiveConfig",
    "ArchiveHostError",
    "Policy",
    "create",
    "id_stub",
    "resolve_extension",
    "store_resource",
]
