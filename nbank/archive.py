# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""
import os
import json
import logging

log = logging.getLogger('nbank')   # root logger

_README_fname = 'README.md'
_config_fname = 'nbank.json'
_config_schema = 'https://melizalab.github.io/neurobank/config.json#'
_resource_subdir = "resources"

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

Deposit resources: `nbank -A archive_path deposit file-1 [file-2 [file-3]]`

Registered or deposited files are given the permissions specified in `project.json`.
However, when entire directories are deposited, ownership and access may not be set correctly.
If you have issues accessing files, run the following commands (usually, as root):
`find resources -type d -exec chmod 2770 {} \+` and `setfacl -R -d -m u::rwx,g::rwx,o::- resources`

"""

_nbank_json = """{
  "$schema": "{schema}",
  "project": {
    "name": null,
    "description": null
  },
  "owner": {
    "name": null,
    "email": null
  },
  "registry": "url_of_registry_endpoint",
  "policy": {
    "auto_identifiers": false,
    "keep_extensions": true,
    "allow_directories": false,
    "access": {
      "user": {user},
      "group": {group},
      "umask": "027"
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
        ret = json.load(open(fname, 'rt'))
        ret["path"] = path
        return ret


def create(archive_path):
    """Initializes a new data archive in archive_path.

    Creates archive_path and all parents as needed. Does not overwrite existing
    files or directories. Raises OSError for failed operations.

    """
    import pwd
    import grp
    import subprocess

    resdir = os.path.join(archive_path, _resource_subdir)
    dircmd = ['mkdir', '-p', resdir]
    ret = subprocess.call(dircmd) # don't expand shell variables/globs
    if ret != 0:
        raise OSError("unable to create archive directories")
    # try to set setgid bit on directory; this fails in some cases
    os.chmod(resdir, 0o2775)
    # try to set default facl; fail silently if setfacl doesn't exist
    faclcmd = "setfacl -d -m -u::rwx,g::rwx,o::- resources".split()
    ret = subprocess.call(faclcmd)

    fname = os.path.join(archive_path, _README_fname)
    if not os.path.exists(fname):
        with open(fname, 'wt') as fp:
            fp.write(_README)

    user = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(os.getgid())
    project_json = _nbank_json.format(schema=_config_schema, user=user.pw_name, group=group.gr_name)
    fname = os.path.join(archive_path, _config_fname)
    if not os.path.exists(fname):
        with open(fname, 'wt') as fp:
            fp.write(project_json)

    fname = os.path.join(archive_path, '.gitignore')
    if not os.path.exists(fname):
        with open(fname, 'wt') as fp:
            fp.writelines(('resources/',))


def id_stub(id):
    """Returns a short version of id, used for sorting objects into subdirectories. """
    return id[:2]


def find_resource(cfg, id):
    """Returns path of the resource specified by id

    The returned filename has the same extension as the resource. If the no such
    resource exists, returns None.

    """
    import glob
    base = os.path.join(cfg["path"], _resource_subdir, id_stub(id), id)
    if os.path.exists(base):
        return base
    for fn in glob.iglob(base + ".*"):
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

    """
    import shutil

    if not cfg['policy']['allow_directories'] and os.path.isdir(src):
        raise ValueError("this archive does not allow directories as resources")

    if id is None:
        id = os.path.basename(src)

    # check for existing resource
    if find_resource(cfg, id) is not None:
        raise KeyError("a file already exists for id %s", id)

    if cfg['policy']['keep_extensions']:
        id = os.path.splitext(id)[0] + os.path.splitext(src)[1]

    log.debug("%s -> %s", src, id)

    tgt_dir = os.path.join(cfg["path"], _resource_subdir, id_stub(id))
    tgt_file = os.path.join(tgt_dir, id)

    # execute commands in this order to prevent data loss; source file is not
    # renamed unless it's copied
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt)
    shutil.move(src, tgt_file)
    chmod(tgt_file, mode)
    return tgt_file


def fix_permissions(cfg, id):
    """Fixes permission bits on resource and its contents, if the resource is a dir

    This is needed because we try to move files whenever possible, so the uid,
    gid, and permission bits often need to be updated.

    """
    import pwd
    import grp

    if os.path.isfile(path):
        os.chmod(path, mode)
    elif os.path.isdir(path):
        assert mode < 0o1000, "invalid permissions mode"
        dirmode = (mode >> 2) | mode
        os.chmod(path, dirmode)
        for root, dirs, files in os.walk(path):
            for dir in dirs:
                os.chmod(os.path.join(root, dir), dirmode)
            for file in files:
                os.chmod(os.path.join(root, file), mode)
