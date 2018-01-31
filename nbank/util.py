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
    import json
    if os.path.exists(fname):
        mapping = json.load(open(fname, 'rU'))
        update_json_data(**kwargs)
    else:
        mapping = kwargs
    json.dump(open(fname, 'wt'), mapping)


# Variables:
# End:


