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
    """Generates an ID from the basename of fname, stripped of any extensions """
    return os.path.splitext(os.path.basename(fname))[0]


def hash(fname, method='sha1'):
    """Returns a hash of the contents of fname using method.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib
    with open(fname, 'rb') as fp:
        return hashlib.new(method, fp.read()).hexdigest()
