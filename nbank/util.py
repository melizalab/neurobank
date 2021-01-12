# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""utility functions

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Tue Jul  8 14:23:35 2014
"""

# python 3 compatibility
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os.path


def id_from_fname(fname):
    """Generates an ID from the basename of fname, stripped of any extensions.

    Raises ValueError unless the resulting id only contains URL-unreserved characters
    ([-_~0-9a-zA-Z])
    """
    import re

    id = os.path.splitext(os.path.basename(fname))[0]
    if re.match(r"^[-_~0-9a-zA-Z]+$", id) is None:
        raise ValueError("resource name '%s' contains invalid characters", id)
    return id


def hash(fname, method="sha1"):
    """Returns a hash of the contents of fname using method.

    fname can be the path to a regular file or a directory.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib

    if os.path.isdir(fname):
        return hash_directory(fname, method)
    with open(fname, "rb") as fp:
        return hashlib.new(method, fp.read()).hexdigest()


def hash_directory(path, method="sha1"):
    """Return hash of the contents of the directory at path using method.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib

    hashes = []
    for fn in sorted(
        os.path.join(pn, fn) for pn, _, fns in os.walk(path) for fn in fns
    ):
        with open(fn, "rb") as fp:
            hashes.append(
                "{}={}".format(
                    os.path.relpath(fn, path),
                    hashlib.new(method, fp.read()).hexdigest(),
                )
            )
    return hashlib.new(method, "\n".join(hashes).encode("utf-8")).hexdigest()
