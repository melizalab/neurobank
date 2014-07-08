# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Script entry points for neurobank

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Tue Nov 26 22:48:58 2013
"""
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os
import sys
import json
import datetime
import logging

from neurobank import nbank, util

log = logging.getLogger('nbank')   # root logger


def init_archive(args):
    log.info("version: %s", nbank.__version__)
    log.info("run time: %s", datetime.datetime.now())
    try:
        nbank.init_archive(args.directory)
    except OSError as e:
        log.error("Error initializing archive: %s", e)
    else:
        log.info("Initialized neurobank archive in %s", os.path.abspath(args.directory))


def store_files(args):
    from neurobank.catalog import _ns as catalog_ns
    log.info("version: %s", nbank.__version__)
    log.info("run time: %s", datetime.datetime.now())

    cfg = nbank.get_config(args.archive)
    if cfg is None:
        log.error("error: %s not a neurobank archive. Use '-A' or set NBANK_PATH in environment.",
                  args.archive)
        return

    if args.read_stdin:
        args.file.extend(l.strip() for l in sys.stdin)

    if len(args.file) == 0:
        log.error("error: no files specified")
        return

    try:
        meta = json.load(open(args.catalog, 'rU'))
        if not meta['namespace'] == catalog_ns:
            raise ValueError("'%s' is not a catalog" % args.catalog)
        files = { e['id'] : e for e in meta['resources'] }
        log.info("opened catalog '%s', resource count %d", args.catalog, len(files))
    except IOError:
        log.info("catalog '%s' doesn't exist, will be created", args.catalog)
        files = {}

    for fname in args.file:
        if not os.path.isfile(fname) and not os.path.isdir(fname):
            log.warn("warning: '%s' is not a file or directory - skipping", fname)
            continue
        path, base, ext = util.fileparts(fname)
        try:
            id = args.func_id(fname)
        except IOError as e:
            if os.path.exists(fname):
                log.warn(
                    "warning: '%s' is a directory, can't be used a a source file - skipping",
                    fname
                )
            else:
                log.warn("warning: '%s' does not exist - skipping", fname)
            continue

        if cfg['policy'][args.target]['keep_filename']:
            id += '_' + base
        if args.suffix:
            id += '_' + args.suffix
        if cfg['policy'][args.target]['keep_extension']:
            id += ext

        try:
            mode = int(cfg['policy'][args.target]['mode'], base=8)
        except (KeyError, ValueError) as e:
            log.warn("E: %s", e)
            mode = 0o440

        tgt = nbank.store_file(fname, args.archive, id, mode)
        if args.target == 'data' and tgt is None:
            # id collisions are errors for data files. This should never happen
            # with uuids
            raise ValueError("id assigned to '%s' already exists in archive: %s" % (fname, id))

        # add id/name mapping to catalog, skipping if it exists
        try:
            if files[id]['name'] != base + ext:
                log.warn("in %s, '%s' is named '%s', not '%s'; keeping old mapping",
                         args.catalog, id, files[id]['name'], base + ext)
            else:
                log.info("'%s' already in %s", id, args.catalog)
        except KeyError:
            files[id] = {'id': id, 'name': base + ext}

        if tgt is not None:
            # file was moved to database
            log.info("%s -> %s", fname, id)
            if args.link:
                try:
                    os.symlink(os.path.abspath(tgt), os.path.join(path, id))
                except OSError as e:
                    log.warn("error creating link: %s", e)
        else:
            log.info("'%s' already in archive as '%s'", fname, id)

    json.dump({'namespace': catalog_ns,
               'version': nbank.fmt_version,
               'resources': list(files.values()),
               'description': '',
               'long_desc': ''},
              open(args.catalog, 'wt'), indent=2, separators=(',', ': '))
    log.info("wrote resource catalog to '%s'", args.catalog)


def id_by_name(args):
    import neurobank.catalog as cat

    for catalog in cat.iter_catalogs(args.archive, args.catalog):
        for match in cat.filter_regex(catalog['value']['resources'], args.regex, 'name'):
            id = match.get('id', None)
            if args.path:
                id = os.path.join(args.archive, id)
            print("%s/%s : %s" % (catalog['key'], match['name'], id))


def props_by_id(args):
    import pprint
    import neurobank.catalog as cat

    for catalog in cat.iter_catalogs(args.archive, args.catalog):
        for match in cat.filter_regex(catalog['value']['resources'], args.regex, 'id'):
            print("%s:" % catalog['key'])
            sys.stdout.write("  ")
            pprint.pprint(match, indent=2)


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description='manage source files and collected data')
    p.add_argument('-v','--version', action="version",
                   version="%(prog)s " + nbank.__version__)
    p.add_argument('-A', '--archive', default=os.environ.get(nbank.env_path, '.'),
                   type=os.path.abspath,
                   help="specify the path of the archive. Default is to use the "
                   "current directory or the value of the environment variable "
                   "%s" % nbank.env_path)

    sub = p.add_subparsers(title='subcommands')

    p_init = sub.add_parser('init', help='initialize a data archive')
    p_init.add_argument('directory',
                        help="path of the (possibly non-existent) directory "
                        "for the archive. If the directory does not exist it's created. "
                        " Does not overwrite any files or directories.")
    p_init.set_defaults(func=init_archive)

    p_reg = sub.add_parser('register', help='register source file(s)')
    p_reg.set_defaults(func=store_files, target='sources', func_id=nbank.source_id)
    p_dep = sub.add_parser('deposit', help='deposit data file(s)')
    p_dep.set_defaults(func=store_files, target='data', func_id=nbank.data_id)

    for psub in (p_reg, p_dep):
        psub.add_argument('--suffix',
                           help='add a constant suffix to the generated identifiers')
        psub.add_argument('--link', action='store_true',
                           help="make links to archived files")
        psub.add_argument('catalog',
                           help="specify a file to store name-id mappings in JSON format. "
                           "If the file exists, new source files are added to it." )
        psub.add_argument('file', nargs='*',
                           help='path of file(s) to add to the repository')
        psub.add_argument('-@', dest="read_stdin", action='store_true',
                           help="read additional file names from stdin")

    p_id = sub.add_parser('search', help='look up name in catalog(s) and return identifiers')
    p_id.add_argument('-p','--path', action="store_true",
                      help="show full paths of resource files")
    p_id.set_defaults(func=id_by_name)

    p_props = sub.add_parser('prop', help='look up properties in catalog(s) by id')
    p_props.set_defaults(func=props_by_id)
    for psub in (p_id, p_props):
        psub.add_argument('-c', '--catalog', action='append', default=None,
                          help="specify one or more metadata catalogs to search for the "
                          "name. Default is to search all catalogs in the archive.")
        psub.add_argument('regex', help='the string or regular expression to match against')

    args = p.parse_args(argv)

    ch = logging.StreamHandler()
    formatter = logging.Formatter("[%(name)s] %(message)s")
    loglevel = logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)  # change
    ch.setFormatter(formatter)
    log.addHandler(ch)

    try:
        args.func(args)
    except AttributeError:
        p.print_usage()


# Variables:
# End:
