# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Script entry points for neurobank

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Tue Nov 26 22:48:58 2013
"""
import os
import sys
import json
import logging

from neurobank import nbank

log = logging.getLogger('nbank')   # root logger

def init_archive(args):
    try:
        nbank.init_archive(args.directory)
    except OSError as e:
        log.error("Error initializing archive: %s", e)
    else:
        log.info("Initialized neurobank archive in %s", os.path.abspath(args.directory))


def store_files(args):
    cfg = nbank.get_config(args.archive)
    if cfg is None:
        log.error("ERROR: %s not a neurobank archive. Use '-A' or set NBANK_PATH.",
                  args.archive)
        return

    if args.read_stdin:
        args.file.extend(l.strip() for l in sys.stdin)

    try:
        meta = json.load(open(args.catalog, 'rU'))
        if not meta['namespace'] == 'neurobank.catalog':
            raise ValueError("'%s' is not a catalog" % args.catalog)
        files = { e['id'] : e for e in meta['files'] }
    except IOError:
        files = {}

    for fname in args.file:
        path, base, ext = nbank.fileparts(fname)
        try:
            id = args.func_id(fname)
        except IOError as e:
            log.warn("E: %s", e)
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

        tgt = nbank.store_file(os.path.join(args.archive, args.target), fname, id, mode)
        if args.target == 'data' and tgt is None:
            # id collisions are errors for data files. This should never happen
            # with uuids
            raise ValueError("id assigned to %s already exists in archive: %s" % (fname, id))

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
            # file was copied to database
            log.info("%s -> %s", fname, id)
            if not args.keep:
                # try to replace file with a symlink
                try:
                    os.remove(fname)
                    os.symlink(os.path.abspath(tgt), os.path.join(path, id))
                except OSError as e:
                    log.error("E: %s", e)
        else:
            log.info("%s already in archive as '%s'", fname, id)

    json.dump({'namespace': 'neurobank.catalog',
               'version': nbank.fmt_version,
               'files': list(files.values())},
              open(args.catalog, 'wt'), indent=2, separators=(',', ': '))
    log.info("Wrote source list to %s", args.catalog)


def main(argv=None):
    import argparse
    import datetime

    p = argparse.ArgumentParser(description='manage source files and collected data')
    sub = p.add_subparsers(title='subcommands')

    p_init = sub.add_parser('init', help='initialize a data archive')
    p_init.add_argument('directory', nargs='?', default='.',
                        help="path of the (possibly non-existent) directory "
                        "for the archive. If the directory does not exist it's created. "
                        "If not supplied the current directory is used. Does not overwrite "
                        "any files or directories.")
    p_init.set_defaults(func=init_archive)

    p_reg = sub.add_parser('register', help='register source file(s)')
    p_reg.set_defaults(func=store_files, target='sources', func_id=nbank.source_id)
    p_dep = sub.add_parser('deposit', help='deposit data file(s)')
    p_dep.set_defaults(func=store_files, target='data', func_id=nbank.data_id)

    for psub in (p_reg, p_dep):
        psub.add_argument('-A', '--archive', default=os.environ.get(nbank.env_path, '.'),
                           type=os.path.abspath,
                           help="specify the path of the archive. Default is to use the "
                           "current directory or the value of the environment variable "
                           "%s" % nbank.env_path)
        psub.add_argument('--suffix',
                           help='add a constant suffix to the generated identifiers')
        psub.add_argument('--keep', action='store_true',
                           help="don't delete source files. By default, files are"
                           "replaced with symlinks to the stored source files")
        psub.add_argument('catalog',
                           help="specify a file to store name-id mappings in JSON format. "
                           "If the file exists, new source files are added to it." )
        psub.add_argument('file', nargs='*',
                           help='path of file(s) to add to the repository')
        psub.add_argument('-@', dest="read_stdin", action='store_true',
                           help="read additional file names from stdin")


    args = p.parse_args(argv)

    ch = logging.StreamHandler()
    formatter = logging.Formatter("[%(name)s] %(message)s")
    loglevel = logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)  # change
    ch.setFormatter(formatter)
    log.addHandler(ch)
    log.info("version: %s", nbank.__version__)
    log.info("run time: %s", datetime.datetime.now())

    args.func(args)


# Variables:
# End:
