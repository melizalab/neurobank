# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""
import os
import json
import logging

log = logging.getLogger("nbank")  # root logger

_README_fname = "README.md"
_config_fname = "nbank.json"
_config_schema = "https://melizalab.github.io/neurobank/config.json#"
_resource_subdir = "resources"
_default_umask = 0o027
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
`find resources -type d -exec chmod 2770 {} \+` and `setfacl -R -d -m u::rwx,g::rwx,o::- resources`

"""

_nbank_json = """{
  "$schema": "%(schema)s",
  "project": {
    "name": null,
    "description": null
  },
  "owner": {
    "name": null,
    "email": null
  },
  "registry": "%(registry_url)s",
  "policy": {
    "auto_identifiers": false,
    "auto_id_type": null,
    "keep_extensions": true,
    "allow_directories": false,
    "require_hash": true,
    "access": {
      "user": "%(user)s",
      "group": "%(group)s",
      "umask": "%(umask)03o"
    }
  }
}
"""


def get_config(path):
    """Returns the configuration for the archive specified by path, or None
    if the path does not refer to a valid neurobank archive.

    """
    fname = os.path.join(path, _config_fname)
    if os.path.exists(fname):
        with open(fname, "rt") as fp:
            ret = json.load(fp)
            umask = ret["policy"]["access"]["umask"]
            if not isinstance(umask, int):
                ret["policy"]["access"]["umask"] = int(
                    ret["policy"]["access"]["umask"], 8
                )
            ret["path"] = path
            return ret


def create(archive_path, registry_url, umask=_default_umask, **policies):
    """Initializes a new data archive in archive_path.

    archive_path: the absolute or relative path of the archive
    registry_url: the URL of the registry service
    umask: the default umask (as an integer)
    **policies: override auto_identifiers, keep_extensions, allow_directories, or require_hash

    Creates archive_path and all parents as needed. Does not overwrite existing
    files or directories. If a config file already exists, uses the umask stored
    there rather than the supplied one. Raises OSError for failed operations.

    """
    import pwd
    import grp
    import subprocess

    cfg = get_config(archive_path)
    if cfg is not None:
        umask = cfg["policy"]["access"]["umask"]

    umask &= 0o777  # mask out the umask

    resdir = os.path.join(archive_path, _resource_subdir)
    dircmd = ["mkdir", "-p", resdir]
    ret = subprocess.call(dircmd)  # don't expand shell variables/globs
    if ret != 0:
        raise OSError("unable to create archive directories")
    os.chmod(archive_path, 0o777 & ~umask)
    # try to set setgid bit on directory; this fails in some cases
    os.chmod(resdir, 0o2777 & ~umask)

    # try to set default facl; fail silently if setfacl doesn't exist
    # FIXME this is not correct if umask is not 007
    faclcmd = "setfacl -d -m u::rwx,g::rwx,o::- {}".format(resdir).split()
    try:
        ret = subprocess.call(faclcmd)
    except FileNotFoundError:
        log.debug("setfacl does not exist on this platform")

    fname = os.path.join(archive_path, _README_fname)
    if not os.path.exists(fname):
        with open(fname, "wt") as fp:
            fp.write(_README)
    os.chmod(fname, 0o666 & ~umask)

    user = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(os.getgid())
    project_json = _nbank_json % dict(
        schema=_config_schema,
        registry_url=registry_url,
        user=user.pw_name,
        group=group.gr_name,
        umask=umask,
    )
    fname = os.path.join(archive_path, _config_fname)
    if not os.path.exists(fname):
        with open(fname, "wt") as fp:
            fp.write(project_json)
    os.chmod(fname, 0o666 & ~umask)

    fname = os.path.join(archive_path, ".gitignore")
    if not os.path.exists(fname):
        with open(fname, "wt") as fp:
            fp.writelines(("resources/",))
    os.chmod(fname, 0o666 & ~umask)


def id_stub(id):
    """Returns a short version of id, used for sorting objects into subdirectories. """
    return id[:2]


def resource_path(archive_path, id):
    """ Returns path of the resource specified by id"""
    return os.path.join(archive_path, _resource_subdir, id_stub(id), id)


def find_resource(path, id=None):
    """Finds the resource specified by path (or by id within an archive)

    This function is needed if the 'keep_extension' policy is True, in which
    case resource 'xyzzy' could refer to a file called 'xyzzy.wav' or
    'xyzzy.json', etc. If no resource associated with the supplied path exists,
    returns None.

    """
    import glob

    if id is not None:
        path = resource_path(path, id)
    if os.path.exists(path):
        return path
    for fn in glob.iglob(path + ".*"):
        return fn


def store_resource(cfg, src, id=None):
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
    import shutil

    if id is None:
        id = os.path.basename(src)

    if not cfg["policy"]["allow_directories"] and os.path.isdir(src):
        raise TypeError("policy forbids depositing directories")

    # check for existing resource
    if find_resource(resource_path(cfg["path"], id)) is not None:
        raise KeyError("a file already exists for id %s", id)

    if cfg["policy"]["keep_extensions"]:
        id = os.path.splitext(id)[0] + os.path.splitext(src)[1]

    log.debug("%s -> %s", src, id)

    tgt_dir = os.path.join(cfg["path"], _resource_subdir, id_stub(id))
    tgt_file = os.path.join(tgt_dir, id)

    # execute commands in this order to prevent data loss; source file is not
    # renamed unless it's copied
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt_dir)
        fix_permissions(cfg, tgt_dir, walk=False)
    shutil.move(src, tgt_file)
    fix_permissions(cfg, tgt_file)
    return tgt_file


def fix_permissions(cfg, tgt, walk=True):
    """Fixes permission bits on resource and its contents, if the resource is a dir

    This is needed because we try to move files whenever possible, so the uid,
    gid, and permission bits often need to be updated.

    """
    import pwd
    import grp

    myuid = os.getuid()
    if myuid == 0:
        uid = pwd.getpwnam(cfg["policy"]["access"]["user"]).pw_uid
    else:
        uid = -1
    gid = grp.getgrnam(cfg["policy"]["access"]["group"]).gr_gid
    umask = cfg["policy"]["access"]["umask"]

    def fix(fname):
        try:
            os.chown(fname, uid, gid)
        except PermissionError:
            log.warn("unable to change uid/gid of '%s'", fname)
        os.chmod(fname, os.stat(fname).st_mode & ~umask)

    fix(tgt)
    for root, dirs, files in os.walk(tgt):
        for dir in dirs:
            fix(os.path.join(root, dir))
        for file in files:
            fix(os.path.join(root, file))
