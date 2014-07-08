# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""
import os
import json

__version__ = '0.2.1'
env_path = "NBANK_PATH"
fmt_version = "1.0"

_README_fname = 'README.md'
_config_fname = 'project.json'
_config_ns = 'neurobank.config'

_README = """
This directory contains a neurobank data management archive. The following
files and directories are part of the archive:

+ README.md: this file
+ project.json: information and configuration for the archive
+ sources/:  registered source files for experiments,
+ data/:     deposited data files
+ metadata/: metadata (stimulus lists, analysis groups, etc) in JSON format

Files in `sources` and `data` are organized into subdirectories based on the
first two characters of the files' identifiers. Source files may have attributes
associated with them stored in JSON files. The name of the attribute file is the
identifier plus `.json`.

# Archive contents

Add notes about the contents of the data archive here. You should also edit
`project.json` to set information and policy for your project.

"""

_project_json = """{
  "namespace": "%s",
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
    "sources": {
      "keep_extension": true,
      "keep_filename": false,
      "mode": "440"
    },
    "data": {
      "keep_extension": true,
      "keep_filename": false,
      "mode": "440"
    }
  }
}
""" % (_config_ns, fmt_version)


def get_config(path):
    """Returns the configuration for the archive specified by path, or None
    if the path does not refer to a valid neurobank archive.

    """
    fname = os.path.join(path, _config_fname)
    if os.path.exists(fname):
        return json.load(open(fname, 'rt'))


def get_source(id, path):
    """Returns the absolute path for a source file with id in the archive under path.

    Does not check the validity of the path or id.
    """
    return os.path.abspath(os.path.join(path, 'sources', id_stub(id), id))


def init_archive(archive_path):
    """Initializes a new data archive in archive_path.

    Creates archive_path and all parents as needed. Does not overwrite existing
    files or directories. Raises OSError for failed operations.

    """
    from catalog import _subdir as catalog_subdir
    import subprocess

    dirs = [os.path.join(archive_path, p) for p in ('sources', 'data', catalog_subdir)]
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

    fname = os.path.join(archive_path, '.gitignore')
    if not os.path.exists(fname):
        with open(fname, 'wt') as fp:
            fp.writelines(('sources/', 'data/'))


def store_file(tgt_dir, fname, id, mode=0o440):
    """Stores fname in the repository under a unique identifier.

    tgt_dir - the directory to store the file
    fname - the path of the file
    id - the identifier of the file
    mode - the file access mode to set for the file once it's in the archive

    Checks whether the object already exists in the archive. If not, copies
    the file to the archive under the identifer and returns the path of the
    archived file. If the identifier is already taken, returns None and takes no
    other action.

    """
    import shutil

    tgt_dir = os.path.join(tgt_dir, id_stub(id))
    tgt_file = os.path.join(tgt_dir, id)
    if os.path.exists(tgt_file):
        return None

    # execute commands in this order to prevent data loss; source file is not
    # renamed unless it's copied
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt_dir)
    shutil.copy2(fname, tgt_file)
    os.chmod(tgt_file, mode)
    return tgt_file


def source_id(fname, method='sha1'):
    """Returns a hash-based identifier for the contents of fname using method.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib
    with open(fname, 'rb') as fp:
        return hashlib.new(method, fp.read()).hexdigest()


def data_id(fname):
    """Returns a uuid-based identifier for a data file.

    If the base filename has the form of a uuid, attempts to use this value;
    otherwise returns a randomly generated uuid.

    """
    import uuid
    try:
        return str(uuid.UUID(hex=os.path.basename(fname)[:36]))
    except ValueError:
        return str(uuid.uuid4())


def id_stub(id):
    """Returns a short version of id, used for sorting objects into subdirectories.

    """
    return id[:2] if isinstance(id, str) else None


def fileparts(fname):
    """Returns components of fname: dirname, basename of fname without extension, and extension"""
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
    """Updates or creates a json file with kwargs mapping

    If fname does not exist, creates a JSON file with the mapping in kwargs. If
    fname does exist, opens it, loads the contents, updates with the kwargs
    mapping, and writes the new data to disk.

    """
    if os.path.exists(fname):
        mapping = json.load(open(fname, 'rU'))
        update_json_data(**kwargs)
    else:
        mapping = kwargs
    json.dump(open(fname, 'wt'), mapping)


# Variables:
# End:
