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


def register_files(args):
    cfg = nbank.get_config(args.archive)
    if cfg is None:
        log.error("ERROR: %s not a neurobank archive. Use '-A' or set NBANK_PATH.",
                  args.archive)
        return

    if args.read_stdin:
        args.file.extend(l.strip() for l in sys.stdin)

    meta = dict(namespace='neurobank.sourcelist',
                version=nbank.fmt_version,
                sources=[])
    for fname in args.file:
        path, base, ext = nbank.fileparts(fname)
        try:
            id = nbank.source_id(fname)
        except IOError as e:
            log.warn("E: %s", e)
            continue

        if cfg['policy']['source']['keep_filename']:
            id += '_' + base
        if args.suffix:
            id += '_' + args.suffix
        if cfg['policy']['source']['keep_extension']:
            id += ext

        tgt = nbank.register_source(args.archive, fname, id)
        meta['sources'].append({'id': id, 'name': base + ext})
        if tgt is not None:
            log.info("%s -> %s", fname, id)
            if not args.keep:
                try:
                    os.symlink(os.path.abspath(tgt), os.path.join(path, id))
                    os.remove(fname)
                except OSError as e:
                    log.error("E: %s", e)
        else:
            log.info("%s already in archive as %s", fname, id)

    fname = args.metafile + '.json'
    json.dump(meta, open(fname, 'wt'), indent=2, separators=(',', ': '))
    log.info("Wrote source list to '%s'", fname)


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

    p_reg = sub.add_parser('register', help='register source files')
    p_reg.add_argument('-A', '--archive', default=os.environ.get(nbank.env_path, '.'),
                       type=os.path.abspath,
                       help="specify the path of the archive. Default is to use the "
                       "current directory or the value of the environment variable "
                       "%s" % nbank.env_path)
    p_reg.add_argument('--suffix',
                       help='add a constant suffix to the generated identifiers')
    p_reg.add_argument('--keep', action='store_true',
                       help="don't delete source files. By default, files are"
                       "replaced with symlinks to the stored source files")
    p_reg.add_argument('metafile',
                       help="specify the name of the output JSON metadata file")
    p_reg.add_argument('file', nargs='+',
                       help='path of file(s) to add to the repository')
    p_reg.add_argument('-', dest="read_stdin", action='store_true',
                       help="read additional file names from stdin")
    p_reg.set_defaults(func=register_files)

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
