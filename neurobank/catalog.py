# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Functions for searching metadata catalogs

Copyright (C) 2014 Dan Meliza <dmeliza@gmail.com>
Created Thu May  8 11:23:36 2014
"""

# python 3 compatibility
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

_ns = "neurobank.catalog"
_subdir = 'metadata'


def filter_regex(arr, regex, key):
    """Returns a lazy sequence of objects in arr where 'key' field matches 'regex'

    Equivalent to (x for x in arr if regex.match(o['key']))

    regex - can be a compiled regular expression or a raw string
    """
    if not hasattr(regex, 'match'):
        import re
        regex = re.compile(regex)
    return (x for x in arr if (key in x and regex.match(x[key]) is not None))


def iter_catalogs(archive, files=None):
    """Returns a lazy sequence of {'key': basename, 'value': json} dicts for
    catalogs in the archive.

    archive - the top level directory of the archive
    files - if specified, only returns catalogs that match names in this list

    """
    import os
    import glob
    import json

    for f in glob.iglob(os.path.join(archive, _subdir, "*.json")):
        basename = os.path.splitext(os.path.basename(f))[0]
        if files is None or basename in files:
            try:
                m = json.load(open(f, 'rU'))
                if m['namespace'] == _ns:
                    yield {'key': basename, 'value': m}
            except (ValueError, KeyError):
                pass




# Variables:
# End:
