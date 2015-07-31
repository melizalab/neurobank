# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Functions for interacting with metadata catalogs

Copyright (C) 2014 Dan Meliza <dmeliza@gmail.com>
Created Thu May  8 11:23:36 2014
"""

# python 3 compatibility
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals
import sys
import pprint

_ns = "neurobank.catalog"
_subdir = 'metadata'

from neurobank.nbank import fmt_version

import logging
log = logging.getLogger("nbank")

def new(resources=[]):
    return {'namespace': _ns,
            'version': fmt_version,
            'resources': resources,
            'description': '',
            'long_desc': ''}

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

def find_by_name(archive, regex, catalogs=None):
    for catalog in iter_catalogs(archive, catalogs):
        for match in filter_regex(catalog['value']['resources'], regex, 'name'):
            yield (catalog['key'], match)

def merge(source, target, no_confirm=False):
    """ Merge source metadata dictionary into target """

    tgt_res = { r['id'] : r for r in target['resources'] }
    for resource in source['resources']:
        id = resource['id']
        if id in tgt_res:
            if tgt_res[id] == resource:
                log.info("no changes for id '%s'", id)
                continue
            log.info("mismatch for id '%s'", id)
            sys.stdout.write("original:\n  ")
            pprint.pprint(tgt_res[id], indent=2)
            sys.stdout.write("new:\n  ")
            pprint.pprint(resource, indent=2)
            inpt = None
            while not no_confirm and inpt not in ('y','Y', 'n', 'N', ''):
                inpt = input("Merge new? (Y/n)? ")
                if no_confirm or inpt in ('y', 'Y', ''):
                    log.info("updated '%s' with new data", id)
                    tgt_res[id].update(resource)
        else:
            log.info("adding id '%s'", id)
            tgt_res[id] = resource

    target['resources'] = list(tgt_res.values())

def search_by(archive, key, pattern):
    """Returns a lazy sequence of objects in archive where key matches pattern"""
    for catalog in iter_catalogs(archive):
        for match in filter_regex(catalog['value']['resources'], pattern, key):
            yield match



# Variables:
# End:
