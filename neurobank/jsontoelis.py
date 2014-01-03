# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Convert event time data in json files to toelis

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Thu Jan  2 10:39:37 2014
"""

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os
import numpy as nx
import toelis
import json
import itertools

def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="Convert json-evt file to toelis format. "
                                "Creates directories named after units, "
                                "and files named after units and stimuli.")
    p.add_argument("-a", "--align",
                   help="align spike times to field (default '%(default)s')",
                   default="stim_on")
    p.add_argument('json', help="input json-evt file")

    opts = p.parse_args(args=argv)

    with open(opts.json, 'rU') as fp:
        data = json.load(fp)

        # sort by unit, stimulus, offset
        trials = sorted(data['trials'], key= lambda x: (x['unit'], x['stim'], x['offset']))
        for unitname, unit in itertools.groupby(trials, lambda x: x['unit']):
            if not os.path.exists(unitname):
                os.mkdir(unitname)
            count = 0
            for stimname, trial in itertools.groupby(unit, lambda x: x['stim']):
                fname = os.path.join(unitname, "%s_%s.toe_lis" % (unitname,
                                                                  os.path.splitext(stimname)[0]))
                events = [nx.asarray(t['events']) - t[opts.align]
                          for t in trial]
                with open(fname, 'wt') as fp:
                    toelis.write(fp, events)
                count += 1
            print("Wrote %d files to %s" % (count, unitname))



if __name__ == '__main__':
    main()

# Variables:
# End:
