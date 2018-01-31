#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-

# python 3 compatibility
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import json
import os
import sys
from neurobank import nbank, catalog

def parse_lines(seq):
    for i,line in enumerate(seq):
        fields = line.strip().split()
        if i == 0:
            keys = fields
        else:
            yield dict((k,v) for k, v in zip(keys, fields))

if __name__ == '__main__':

    import argparse
    p = argparse.ArgumentParser("convert table to json catalog, looking up ids from names")
    p.add_argument('-A', '--archive', default=os.environ.get(nbank.env_path, '.'),
                   type=os.path.abspath,
                   help="specify the path of the archive. Default is to use the "
                   "current directory or the value of the environment variable "
                   "%s" % nbank.env_path)
    p.add_argument("table", help="file to read")
    opts = p.parse_args()

    resources = []
    for obj in parse_lines(open(opts.table, "rU")):
        if "name" in obj:
            ids = set(x.get("id", None) for x in (catalog.search_by(opts.archive, "name", obj["name"])))
            if len(ids) != 1:
                print("warning: multiple ids match name {}".format(obj["name"]),
                      file=sys.stderr)
            else:
                obj["id"] = ids.pop()
        resources.append(obj)

    json.dump(resources, sys.stdout, indent=2, separators=(',', ': '))
