# -*- mode: python -*-
"""functions for managing a data archive on the local filesystem

Copyright (C) 2013-2025 Dan Meliza <dan@meliza.org>
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, NewType, Optional, Union

log = logging.getLogger("nbank")  # root logger

ArchiveConfig = NewType("ArchiveConfig", Dict)
_README_fname = "README.md"
_config_fname = "nbank.json"
_config_schema = "https://melizalab.github.io/neurobank/config.json#"
_resource_subdir = "resources"
_default_umask = 0o002
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

Registered or deposited files are given the permissions specified in `project.json`.
However, when entire directories are deposited, ownership and access may not be set correctly.
If you have issues accessing files, run the following commands (usually, as root):
`find resources -type d -exec chmod 2775 {} \\+` and `setfacl -R -d -m u::rwx,g::rwx,o::rx resources`

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


def create(
    archive_path: Path,
    registry_url: str,
    umask: int = _default_umask,
    **policies: Any,
) -> ArchiveConfig:
    """Initializes a new data archive in archive_path.

    archive_path: the absolute or relative path of the archive
    registry_url: the URL of the registry service
    umask: the default umask (as an integer)
    **policies: override auto_identifiers, keep_extensions, allow_directories, or require_hash

    Creates archive_path and all parents as needed. Does not overwrite existing
    files or directories. If a config file already exists, uses the umask stored
    there rather than the supplied one. Raises OSError for failed operations.

    Returns the config dict for the archive

    """
    import grp
    import pwd
    import subprocess
    from os import getgid, getuid

    archive_path = archive_path.resolve(strict=False)
    try:
        cfg = get_config(archive_path)
        umask = cfg["policy"]["access"]["umask"]
    except FileNotFoundError:
        pass

    umask &= 0o777  # mask out the umask

    resdir = archive_path / _resource_subdir
    resdir.mkdir(umask, parents=True, exist_ok=True)
    # try to set setgid bit on directory; this fails in some cases
    resdir.chmod(0o2777 & ~umask)

    # try to set default facl; fail silently if setfacl doesn't exist
    # FIXME this is not correct if umask is not 005
    faclcmd = f"setfacl -d -m u::rwx,g::rwx,o::rx {resdir}".split()
    try:
        _ = subprocess.call(faclcmd)
    except FileNotFoundError:
        log.debug("setfacl does not exist on this platform")

    fname = archive_path / _README_fname
    fname.write_text(_README)
    fname.chmod(0o666 & ~umask)

    user = pwd.getpwuid(getuid())
    group = grp.getgrgid(getgid())
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
            "access": {"user": user.pw_name, "group": group.gr_name, "umask": umask},
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

    return get_config(archive_path)


def id_stub(id: str) -> str:
    """Returns a short version of id, used for sorting objects into subdirectories."""
    return id[:2]


def resource_path(
    cfg: Union[ArchiveConfig, Path, str], id: str, resolve_ext: bool = False
) -> Path:
    """Returns path of the resource specified by id"""
    try:
        root = cfg["path"]
    except TypeError:
        root = Path(cfg)
    partial = root / _resource_subdir / id_stub(id) / id
    if not resolve_ext:
        return partial
    else:
        return resolve_extension(partial)


def resolve_extension(path: Path) -> Path:
    """Resolves the full path including extension of a resource.

    This function is needed if the 'keep_extension' policy is True, in which
    case resource 'xyzzy' could refer to a file called 'xyzzy.wav' or
    'xyzzy.json', etc. If no resource associated with the supplied path exists,
    raises FileNotFoundError.

    """
    if path.exists():
        return path
    paths = path.parent.glob(f"{path.name}.*")
    try:
        return next(paths)
    except StopIteration as err:
        raise FileNotFoundError(f"resource '{path}' does not exist") from err


def iter_resources(path: Path) -> Iterator[Path]:
    base_dir = path / _resource_subdir
    for stub_dir in base_dir.iterdir():
        yield from stub_dir.iterdir()


class Resource:
    """A resource stored in a local neurobank archive.

    The `root` field of the location is interpreted as being the path of
    a neurobank archive on the local file system. If the `alt_base` parameter is
    set, the dirname of the root will be replaced with this value; e.g.
    alt_base='/scratch' will change '/home/data/starlings' to
    '/scratch/starlings'. This is intended to be used with temporary copies of
    archives on other hosts.


    """

    schemes = ("neurobank",)
    local = True

    def __init__(self, root: str, id: str, alt_base: Optional[Path] = None):
        root = Path(root)
        if alt_base is not None:
            root = Path(alt_base) / root.name
        self.path = resource_path(root, id, resolve_ext=True)

    def __str__(self):
        return str(self.path)

    @property
    def deletable(self) -> bool:
        return os.access(self.path.parent, os.W_OK)

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
        if self.path.is_dir():
            shutil.rmtree(self.path)
        else:
            self.path.unlink()


def check_permissions(cfg: ArchiveConfig, src: Path, id: Optional[str] = None) -> bool:
    """Check if src file can be deposited in an archive."""
    import os

    if not os.access(src, os.R_OK):
        return False
    reqd_perms = os.R_OK | os.W_OK | os.X_OK
    if id is None:
        id = src.name
    tgt_base = cfg["path"] / _resource_subdir
    tgt_dir = tgt_base / id_stub(id)
    if not os.access(tgt_base, os.F_OK) or not os.access(tgt_base, reqd_perms):
        return False
    if os.access(tgt_dir, os.F_OK) and not os.access(tgt_dir, reqd_perms):
        return False
    else:
        return True


def store_resource(cfg: ArchiveConfig, src: Path, id: Optional[str] = None) -> Path:
    """Stores resource (src) in the repository under a unique identifier.

    cfg - the configuration dict for the archive
    src - the path of the file or directory
    id - the identifier for the resource. If None, the basename of src is used

    This function just takes care of moving the resource into the archive;
    caller is responsible for making sure id is valid. Errors will be raised
    if a resource matching the identifier already exists, or if the request
    violates the archive policies on directories. Extensions are stripped or
    added to filenames according to policy.

    NB: the policy on disk can always be overridden by modifying the config
    dictionary. This avoids reading and parsing the file repeatedly, but could be
    exploited by a malicious caller.

    """
    from shutil import move

    if not cfg["policy"]["allow_directories"] and src.is_dir():
        raise TypeError("policy forbids depositing directories")

    if id is None:
        id = src.name

    if cfg["policy"]["keep_extensions"]:
        id = Path(id).stem + src.suffix

    # check for existing resource
    try:
        _ = resource_path(cfg, id, resolve_ext=True)
    except FileNotFoundError:
        pass
    else:
        raise KeyError("a file already exists for id %s", id)
    log.debug("%s -> %s", src, id)

    # execute commands in this order to prevent data loss; source file is not
    # renamed unless it's copied
    pfix = permission_fixer(cfg)
    tgt_dir = cfg["path"] / _resource_subdir / id_stub(id)
    try:
        tgt_dir.mkdir(parents=True)
        pfix(tgt_dir)
    except FileExistsError:
        pass

    tgt_file = tgt_dir / id
    move(src, tgt_file)
    pfix(tgt_file)
    if tgt_file.is_dir():
        for f in tgt_file.rglob("*"):
            pfix(f)

    return tgt_file


def permission_fixer(cfg: ArchiveConfig):
    """Returns a function that will fix ownership/permissions for a resource or containing directory."""
    import grp
    import pwd
    from os import chown, getuid

    myuid = getuid()
    if myuid == 0:
        uid = pwd.getpwnam(cfg["policy"]["access"]["user"]).pw_uid
    else:
        uid = -1
    gid = grp.getgrnam(cfg["policy"]["access"]["group"]).gr_gid
    umask = cfg["policy"]["access"]["umask"]

    def fix(p: Path) -> None:
        try:
            chown(p, uid, gid)
            p.chmod(p.stat().st_mode & ~umask)
        except PermissionError:
            log.warning("unable to change uid/gid or permissions of %s", p)

    return fix


__all__ = [
    "check_permissions",
    "create",
    "get_config",
    "id_stub",
    "resolve_extension",
    "store_resource",
]
