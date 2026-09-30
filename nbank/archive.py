# -*- mode: python -*-
"""functions for managing a data archive on the local filesystem

Copyright (C) 2013-2025 Dan Meliza <dan@meliza.org>
"""

import json
import logging
import os
import shutil
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NewType

from nbank.types import location_scheme

log = logging.getLogger("nbank")  # root logger

ArchiveConfig = NewType("ArchiveConfig", dict)
_README_fname = "README.md"
_config_fname = "nbank.json"
_config_schema = "https://melizalab.github.io/neurobank/config.json#"
_resource_subdir = "resources"
_default_umask = 0o002
# Linux needs setgid on directories for new files to inherit their group. BSD
# and macOS always inherit the group of the parent directory.
_setgid = stat.S_ISGID if sys.platform.startswith("linux") else 0
_README = """
This directory contains a [neurobank](https://github.com/melizalab/neurobank)
data management archive. The following files and directories are part of the archive:

 + README.md: this file
 + nbank.json: information and configuration for the archive
 + resources/:  registered source files and deposited data

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


def get_config(path: Path) -> ArchiveConfig:
    """Returns the configuration for the archive specified by path."""
    fname = path / _config_fname

    with open(fname) as fp:
        ret = json.load(fp)
        umask = ret["policy"]["access"]["umask"]
        if not isinstance(umask, int):
            ret["policy"]["access"]["umask"] = int(ret["policy"]["access"]["umask"], 8)
        ret["path"] = path.resolve(strict=True)
        return ret


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
    umask: int = _default_umask,
    *,
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
    read-only. policies can set auto_identifiers, keep_extensions, allow_directories, or
    require_hash.

    Creates archive_path and its parents as needed. Raises FileExistsError if any file
    it would create already exists, ValueError if group doesn't exist, and OSError for
    other failures.
    """
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
            "keep_extensions": True,
            "allow_directories": False,
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
    fname.write_text("resources/\n")
    fname.chmod(0o666 & ~umask)

    cfg = get_config(archive_path)
    permission_fixer(cfg)(resdir)
    for name in _created_files:
        try:
            chown(archive_path / name, -1, gid)
        except PermissionError:
            log.warning("unable to change the group of %s", archive_path / name)
    return cfg


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
    try:
        root = cfg["path"]
    except TypeError:
        root = Path(cfg)
    partial = root / _resource_subdir / id_stub(name) / name
    if not resolve_ext:
        return partial
    else:
        return resolve_extension(partial)


def new_resource_path(cfg: ArchiveConfig, id: str, source_name: str) -> Path:
    """Returns the path to store a new resource at.

    The name is the id, plus the extension of source_name if the archive keeps
    extensions. Doesn't check whether the resource is already stored.
    """
    if cfg["policy"]["keep_extensions"]:
        return resource_path(cfg, Path(id).stem + Path(source_name).suffix)
    return resource_path(cfg, id)


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


def iter_resources(path: Path) -> Iterator[Path]:
    """Yields the files in the archive at path.

    Deprecated: raises NotADirectoryError if the resources directory contains a file.
    Use nbank.check.check_archive_contents instead.
    """
    import warnings

    warnings.warn(
        "iter_resources is deprecated; use nbank.check.check_archive_contents",
        DeprecationWarning,
        stacklevel=2,
    )
    base_dir = path / _resource_subdir
    return (f for stub_dir in base_dir.iterdir() for f in stub_dir.iterdir())


@location_scheme
class Resource:
    """A resource stored in a local neurobank archive.

    The location's root is the path of the archive. alt_base replaces the directory that
    contains the archive, e.g. alt_base='/scratch' maps '/home/data/starlings' to
    '/scratch/starlings', for copies of archives on other hosts.
    """

    schemes = ("neurobank",)
    local = True

    def __init__(self, root: str, id: str, alt_base: Path | None = None):
        root = Path(root)
        if alt_base is not None:
            root = Path(alt_base) / root.name
        self.id = id
        self.path = resource_path(root, id, resolve_ext=True)

    @classmethod
    def from_location(
        cls, location, *, alt_base=None, http_session=None
    ) -> "Resource | None":
        """Returns None if the archive or the resource isn't on this host."""
        try:
            return cls(location["root"], location["resource_name"], alt_base)
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

    For a directory resource, this includes making read-only directories inside it
    writable.
    """
    if not os.access(path.parent, os.W_OK | os.X_OK):
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

    Read-only directories inside a directory resource are made writable first. Raises
    PermissionError if that isn't allowed.
    """
    if not path.is_dir() or path.is_symlink():
        path.unlink()
        return
    for d in _directories_in(path):
        if not os.access(d, os.W_OK | os.X_OK):
            d.chmod(stat.S_IMODE(d.stat().st_mode) | stat.S_IRWXU)
    shutil.rmtree(path)


def _same_filesystem(a: Path, b: Path) -> bool:
    """True if a and b are on the same filesystem (mount)."""
    return a.stat().st_dev == b.stat().st_dev


def verify_permissions(cfg: ArchiveConfig, src: Path, id: str | None = None) -> None:
    """Raises PermissionError, naming the path and problem, if src can't be deposited.

    src must be readable, and the resources directory (and id's subdirectory, if it
    exists) readable, writable, and searchable. For a directory going to another
    filesystem, everything inside it must be readable too.
    """
    if not os.access(src, os.R_OK):
        raise PermissionError(f"'{src}' is not readable")
    reqd_perms = os.R_OK | os.W_OK | os.X_OK
    if id is None:
        id = src.name
    tgt_base = cfg["path"] / _resource_subdir
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
    if src.is_dir() and not _same_filesystem(src, tgt_base):
        for item in src.rglob("*"):
            perm = (os.R_OK | os.X_OK) if item.is_dir() else os.R_OK
            if not os.access(item, perm):
                raise PermissionError(f"'{item}' is not readable")


def verify_no_symlinks(src: Path) -> None:
    """Raises ValueError, naming the link, if src is or contains a symbolic link."""
    if src.is_symlink():
        raise ValueError(f"'{src}' is a symbolic link")
    if src.is_dir():
        for path in src.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"'{path}' is a symbolic link")


def store_resource(cfg: ArchiveConfig, src: Path, id: str | None = None) -> Path:
    """Moves src into the archive as id and returns the stored path.

    id defaults to the name of src. Doesn't register anything: the caller makes sure id
    is valid and registered. The stored name keeps the extension of src if the policy
    keeps extensions. If the directory holding src isn't writable, src is copied instead
    and left in place, with a warning. Policies come from cfg, not from the archive's
    nbank.json.

    Raises KeyError if a file is already stored for id, and TypeError if src is a
    directory and the policy doesn't allow them.
    """
    if not cfg["policy"]["allow_directories"] and src.is_dir():
        raise TypeError("policy forbids depositing directories")

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
        if src.is_dir():
            shutil.copytree(src, tgt_file, symlinks=True)
        else:
            shutil.copy2(src, tgt_file)
    pfix(tgt_file)
    if tgt_file.is_dir():
        for f in tgt_file.rglob("*"):
            pfix(f)

    return tgt_file


def mode_policy(cfg: ArchiveConfig, path: Path) -> tuple[int, int]:
    """Returns (required, forbidden) mode bits for path under the archive's policy.

    Nothing may have bits the umask forbids. The resources directory and its
    subdirectories need every bit the umask allows, plus setgid on Linux. Other
    directories must be readable and searchable, and files readable, by everyone the
    umask allows. With read_only_resources, resources and everything in them must not be
    writable.
    """
    access = cfg["policy"]["access"]
    umask = access["umask"]
    base = cfg["path"] / _resource_subdir
    try:
        in_resource = len(path.relative_to(base).parts) >= 2
    except ValueError:
        in_resource = False
    forbidden = umask
    if in_resource and access.get("read_only_resources", False):
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

    user = cfg["policy"]["access"].get("user")
    if user is not None and getuid() == 0:
        uid = pwd.getpwnam(user).pw_uid
    else:
        uid = -1
    gid = grp.getgrnam(cfg["policy"]["access"]["group"]).gr_gid

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
    "create",
    "get_config",
    "id_stub",
    "resolve_extension",
    "store_resource",
]
