# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""

def init_archive(archive_path):
    """Initializes a new data archive in archive_path.

    Creates archive_path and all parents as needed. Does not overwrite existing
    files or directories. Raises OSError for failed operations.

    """
    import os.path
    import subprocess
    # TODO directory for metadata?
    dirs = [os.path.join(archive_path, p) for p in ('sources', 'data')]
    dircmd = ['mkdir', '-p'] + dirs
    ret = subprocess.call(dircmd) # don't expand shell variables/globs
    if ret != 0:
        raise OSError("unable to create archive directories")
    # TODO create template README.md and config files


def register_source(archive_path, fname, id):
    """Registers fname as a source file in the repository under a unique identifier.

    Checks whether the identifier already exists in the archive. If not, copies
    the file to the archive under the identifer and creates a soft link to the
    archived file (using the absolute path, so the link is relocatable). Returns
    the path of the archived file. If the identifier is already taken, returns
    None and takes no other action.

    """
    import os
    import shutil

    pn, fn = os.path.split(fname)
    base, ext = os.path.splitext(fn)

    id += ext                   # file keeps its extension
    tgt_dir = os.path.join(archive_path, "sources", id_stub(id))
    tgt_file = os.path.join(tgt_dir, id)
    if os.path.exists(tgt_file):
        return None

    # execute commands in this order to prevent data loss; source file is not
    # renamed unless it's copied
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt_dir)
    shutil.copy2(fname, tgt_file)
    os.symlink(os.path.abspath(tgt_file), os.path.join(pn, id))
    return tgt_file


def deposit_data(archive_path, fname):
    """Record a data file in the archive.

    Arguments:
    - `archive_path`:
    - `fname`:
    """
    pass


def source_id(fname, suffix=None, method='sha1'):
    """Returns a hash-based identifier for the contents of fname using method.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods. The identifier retains the
    original extension of the file.

    Arguments:
    - `fname`: the path of the file to register
    - `suffix`: if not None, add this string to the end of the identifer

    """
    import hashlib
    with open(fname, 'rb') as fp:
        id = hashlib.new(method, fp.read()).hexdigest()
        if suffix:
            return "%s_%s" % (id, suffix)
        else:
            return id


def id_stub(id):
    """Returns a short version of id, used for sorting objects into subdirectories.

    """
    return id[:3] if isinstance(id, basestring) else None


# Variables:
# End:
