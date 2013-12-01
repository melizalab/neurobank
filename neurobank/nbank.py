# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""
import json

_README_fname = 'README.md'
_config_fname = 'project.json'
_fmt_version = "1.0"

_README = """
This directory contains a neurobank data management archive. The following
files and directories are part of the archive:

+ README.md: this file
+ project.json: configuration for the archive
+ sources/:  directory with registered source files for experiments,
+ data/:     directory with deposited data files
+ metadata/: directory with stored JSON metadata (stimulus lists, analysis groups,
             etc)

Files in sources and data are organized into subdirectories based on the first
three characters of the files' identifiers.

# Archive contents

Add notes about the contents of the data archive here. You should also edit
`project.json` to set information and policy for your project.

"""

_project_json = """{
  "namespace": "neurobank.config",
  "version": "%s",
  "project": {
    "name": null,
    "description": null
  },
  "owner": {
    "name": null,
    "email": null
  },
  "policy": {
    "source": {
      "keep_extension": true,
      "keep_filename": false
    }
  }
}
""" % _fmt_version


def fileparts(fname):
    """Returns the dirname, basename of fname without extension, and extension"""
    import os.path
    pn, fn = os.path.split(fname)
    base, ext = os.path.splitext(fn)
    return pn, base, ext


def update_json_data(mapping, **kwargs):
    """Update the values in a json mapping with kwargs using the following rules:

    - If a key is absent in the map, adds it
    - If a key is present and has a scalar value, compares to the new value,
      raising an error if the value doesn't match
    - If the key is present and the value is a list, appends new items to the list
    - If the key is present and is a dictionary, calls .update() with the new value

    Modifies the mapping in place, so not safe for concurrent calls
    """
    for key, val in kwargs.items():
        if key not in mapping:
            mapping[key] = val
        elif isinstance(val, dict):
            mapping[key].update(val)
        elif isinstance(val, list):
            mapping[key].extend(val)
        elif val != mapping[key]:
            raise ValueError("mapping value for %s (%s) doesn't match argument value (%s)" %
                             (key, val, kwargs[key]))


def update_json_file(fname, **kwargs):
    """Update or create a json file with kwargs mapping

    If fname does not exist, creates a JSON file with the mapping in kwargs. If
    fname does exist, opens it, loads the contents, updates with the kwargs
    mapping, and writes the new data to disk.

    """
    import os.path
    if os.path.exists(fname):
        mapping = json.load(open(fname, 'rU'))
        update_json_data(**kwargs)
    else:
        mapping = kwargs
    json.dump(open(fname, 'wt'), mapping)


def archive_config(path):

    """Returns the configuration for the archive specified by path, or None
    if the path does not refer to a valid neurobank archive.

    """
    import os.path
    fname = os.path.join(path, _config_fname)
    if os.path.exists(fname):
        return json.load(open(fname, 'rt'))


def init_archive(archive_path):
    """Initializes a new data archive in archive_path.

    Creates archive_path and all parents as needed. Does not overwrite existing
    files or directories. Raises OSError for failed operations.

    """
    import os.path
    import subprocess

    dirs = [os.path.join(archive_path, p) for p in ('sources', 'data', 'metadata')]
    dircmd = ['mkdir', '-p'] + dirs
    ret = subprocess.call(dircmd) # don't expand shell variables/globs
    if ret != 0:
        raise OSError("unable to create archive directories")

    fname = os.path.join(archive_path, _README_fname)
    if not os.path.exists(fname):
        with open(fname, 'wt') as fp:
            fp.write(_README)

    fname = os.path.join(archive_path, _config_fname)
    if not os.path.exists(fname):
        with open(fname, 'wt') as fp:
            fp.write(_project_json)


def register_source(archive_path, fname, id):
    """Registers fname as a source file in the repository under a unique identifier.

    Checks whether the identifier already exists in the archive. If not, copies
    the file to the archive under the identifer and returns the path of the
    archived file. If the identifier is already taken, returns None and takes no
    other action.

    """
    import os
    import shutil

    tgt_dir = os.path.join(archive_path, "sources", id_stub(id))
    tgt_file = os.path.join(tgt_dir, id)
    if os.path.exists(tgt_file):
        return None

    # execute commands in this order to prevent data loss; source file is not
    # renamed unless it's copied
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt_dir)
    shutil.copy2(fname, tgt_file)
    return tgt_file


def deposit_data(archive_path, fname):
    """Record a data file in the archive.

    Arguments:
    - `archive_path`:
    - `fname`:
    """
    pass


def source_id(fname, method='sha1'):
    """Returns a hash-based identifier for the contents of fname using method.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib
    with open(fname, 'rb') as fp:
        return hashlib.new(method, fp.read()).hexdigest()


def id_stub(id):
    """Returns a short version of id, used for sorting objects into subdirectories.

    """
    return id[:3] if isinstance(id, basestring) else None


# Variables:
# End:
