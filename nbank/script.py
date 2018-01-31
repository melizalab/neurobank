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

try:
    input = raw_input
except NameError:
    pass

import os
import sys
import json
import datetime
import logging
import pprint

from neurobank import nbank, util
import neurobank.catalog as cat

log = logging.getLogger('nbank')   # root logger


def init_archive(args):
    log.info("version: %s", nbank.__version__)
    log.info("run time: %s", datetime.datetime.now())
    nbank.init_archive(args.directory)
    log.info("Initialized neurobank archive in %s", os.path.abspath(args.directory))


def store_files(args):
    log.info("version: %s", nbank.__version__)
    log.info("run time: %s", datetime.datetime.now())

    cfg = nbank.get_config(args.archive)
    if cfg is None:
        raise ValueError("%s not a neurobank archive. Use '-A' or set NBANK_PATH in environment." %
                         args.archive)

    if args.read_stdin:
        args.file.extend(l.strip() for l in sys.stdin)

    if len(args.file) == 0:
        raise ValueError("no files specified")

    if os.path.exists(args.catalog):
        raise ValueError("catalog '%s' already exists. Write to a new file, then merge" % args.catalog)

    files = []
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

        files.append({'id': id, 'name': base + ext})

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

    json.dump(cat.new(files), open(args.catalog, 'wt'), indent=2, separators=(',', ': '))
    log.info("wrote resource catalog to '%s'", args.catalog)


def id_by_name(args):
    for catalog, match in cat.find_by_name(args.archive, args.regex, args.catalog):
        id = match.get('id', None)
        if args.path:
            print(os.path.join(args.archive, nbank.find_resource(id)))
        else:
            print("%s/%s : %s" % (catalog, match['name'], id))


def props_by_id(args):
    for catalog in cat.iter_catalogs(args.archive, args.catalog):
        for match in cat.filter_regex(catalog['value']['resources'], args.regex, 'id'):
            print("%s:" % catalog['key'])
            sys.stdout.write("  ")
            pprint.pprint(match, indent=2)


def merge_cat(args):
    if not os.path.exists(args.target):
        args.target = os.path.join(args.archive, cat._subdir, os.path.basename(args.target))

    if not os.path.exists(args.target):
        tgt = cat.new()
    else:
        tgt = json.load(open(args.target, 'rU'))
        if not tgt['namespace'] == cat._ns:
            raise ValueError("'%s' is not a catalog" % args.target)
    log.info("appending to '%s', resources=%d", args.target, len(tgt['resources']))

    for source in args.source:
        src = json.load(open(source, 'rU'))
        if not src['namespace'] == cat._ns:
            log.error("'%s' is not a catalog; skipping" % source)
            continue
        log.info("reading from '%s', resources=%d", source, len(src['resources']))
        cat.merge(src, tgt, args.no_confirm)

    json.dump(tgt, open(args.target, 'wt'), indent=2, separators=(',', ': '))
    log.info("wrote merged catalog '%s', resources=%d", args.target, len(tgt['resources']))


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

    p_merge = sub.add_parser('catalog', help="merge catalog into archive metadata")
    p_merge.set_defaults(func=merge_cat)
    p_merge.add_argument("-y","--no-confirm", help="merge new data without asking for confirmation",
                         action="store_true")
    p_merge.add_argument("source", help="the JSON file to merge into the catalog", nargs='+')
    p_merge.add_argument("target", help="the target catalog (just the filename). If the "
                         "file doesn't exist, it's created")


    args = p.parse_args(argv)

    ch = logging.StreamHandler()
    formatter = logging.Formatter("[%(name)s] %(message)s")
    loglevel = logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)  # change
    ch.setFormatter(formatter)
    log.addHandler(ch)

    if not hasattr(args, 'func'):
        p.print_usage()
    else:
        try:
            args.func(args)
        except Exception as e:
            raise
            log.error("error: %s", e)



# Variables:
# End:
