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
import argparse
import requests as rq

import nbank
from nbank import archive, registry

log = logging.getLogger('nbank')   # root logger


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


def userpwd(arg):
    """ If arg is of the form username:password, returns them as a tuple. Otherwise None. """
    ret = arg.split(':')
    return tuple(ret) if len(ret) == 2 else None


def octalint(arg):
    return int(arg, base=8)


class ParseKeyVal(argparse.Action):

    def __call__(self, parser, namespace, arg, option_string=None):
        kv = getattr(namespace, self.dest)
        if kv is None:
            kv = dict()
        if not arg.count('=') == 1:
            raise ValueError(
                "-k %s argument badly formed; needs key=value" % arg)
        else:
            key, val = arg.split('=')
            kv[key] = val
        setattr(namespace, self.dest, kv)


def main(argv=None):

    p = argparse.ArgumentParser(description='manage source files and collected data')
    p.add_argument('-v','--version', action="version",
                   version="%(prog)s " + nbank.__version__)
    p.add_argument('-r', dest='registry_url', help="URL of the registry service. "
                    "Default is to use the environment variable '%s'" % nbank.env_registry,
                    default=os.environ.get(nbank.env_registry, None))
    p.add_argument('-a', dest='auth', help="username:password to authenticate with registry. "
                   "If not supplied, will attempt to use .netrc file",
                   type=userpwd, default=None)

    # p.add_argument('-A', dest='archive', default=os.environ.get(nbank.env_path, '.'),
    #                type=os.path.abspath,
    #                help="specify the path of the archive. Default is to use the "
    #                "current directory or the value of the environment variable "
    #                "%s" % nbank.env_path)

    sub = p.add_subparsers(title='subcommands')

    pp = sub.add_parser('init', help='initialize a data archive')
    pp.set_defaults(func=init_archive)
    pp.add_argument('directory',
                        help="path of the directory for the archive. "
                        "The directory should be empty or not exist. ")
    pp.add_argument('-n', dest='name', help="name to give the archive in the registry. "
                        "The default is to use the directory name of the archive.",
                        default=None)
    pp.add_argument('-u', dest='umask', help="umask for newly created files in archive, "
                        "as an octal. The default is %(default)03o.",
                        type=octalint, default=archive._default_umask)

    pp = sub.add_parser('deposit', help='deposit resource(s)')
    pp.set_defaults(func=store_resources)
    pp.add_argument('directory', help="path of the archive ")
    pp.add_argument('-d','--dtype', help="specify the datatype for the deposited resources")
    pp.add_argument('-H','--hash', action="store_true",
                    help="calculate a SHA1 hash of each file and store in the registry")
    pp.add_argument('-A','--auto-id', action="store_true",
                    help="ask the registry to generate an id for each resource")
    pp.add_argument('-k', help="specify metadata field (use multiple -k for multiple values",
                    action=ParseKeyVal, default=dict(), metavar="KEY=VALUE", dest='metadata')
    pp.add_argument('-j', "--json-out", action="store_true",
                    help="output each deposited file to stdout as line-deliminated JSON")
    pp.add_argument('-@', dest="read_stdin", action='store_true',
                       help="read additional file names from stdin")
    pp.add_argument('file', nargs='+',
                       help='path of file(s) to add to the repository')

    pp = sub.add_parser('locate', help="locate resource(s)")
    pp.set_defaults(func=find_resources)
    pp.add_argument("id", help="the identifier of the resource", nargs='+')

    pp = sub.add_parser('dtype', help='list and add data types')
    ppsub = pp.add_subparsers(title='subcommands')

    pp = ppsub.add_parser('list', help='list datatypes')
    pp.set_defaults(func=list_datatypes)

    pp = ppsub.add_parser('add', help='add datatype')
    pp.add_argument("dtype_name", help="a unique name for the data type")
    pp.add_argument("content_type", help="the MIME content-type for the data type")
    pp.set_defaults(func=add_datatype)

    # p_id = sub.add_parser('search', help='look up name in catalog(s) and return identifiers')
    # p_id.add_argument('-p','--path', action="store_true",
    #                   help="show full paths of resource files")
    # p_id.set_defaults(func=id_by_name)

    # p_props = sub.add_parser('prop', help='look up properties in catalog(s) by id')
    # p_props.set_defaults(func=props_by_id)
    # for psub in (p_id, p_props):
    #     psub.add_argument('-c', '--catalog', action='append', default=None,
    #                       help="specify one or more metadata catalogs to search for the "
    #                       "name. Default is to search all catalogs in the archive.")
    #     psub.add_argument('regex', help='the string or regular expression to match against')

    # p_merge = sub.add_parser('catalog', help="merge catalog into archive metadata")
    # p_merge.set_defaults(func=merge_cat)
    # p_merge.add_argument("-y","--no-confirm", help="merge new data without asking for confirmation",
    #                      action="store_true")
    # p_merge.add_argument("source", help="the JSON file to merge into the catalog", nargs='+')
    # p_merge.add_argument("target", help="the target catalog (just the filename). If the "
    #                      "file doesn't exist, it's created")


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
        return 0

    # some of the error handling is common; sub-funcs should only catch specific errors
    try:
        args.func(args)
    except rq.exceptions.ConnectionError as e:
        log.error("registry error: unable to contact server")
    except rq.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            log.error("registry error: authenticate required with '-a username:password' or .netrc file")
        else:
            log.error("internal registry error:")
            raise e
    except RuntimeError as e:
        log.error("MAJOR ERROR: archive may have become corrupted")
        raise e


def init_archive(args):
    log.debug("version: %s", nbank.__version__)
    log.debug("run time: %s", datetime.datetime.now())
    args.directory = os.path.abspath(args.directory)
    if args.name is None:
        args.name = os.path.basename(args.directory)

    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", nbank.env_registry)
        return

    try:
        registry.add_domain(args.registry_url, args.name, registry._neurobank_scheme,
                            args.directory, args.auth)
    except rq.exceptions.HTTPError as e:
        # bad request means the domain name is taken or badly formed
        if e.response.status_code == 400:
            log.error("unable to create domain. Name must match [0-9a-zA-Z_-]+, and name and path must be unique")
        else:
            raise e
    else:
        log.info("registered '%s' as domain '%s'", args.directory, args.name)
        archive.create(args.directory, args.registry_url, args.umask)
        log.info("initialized neurobank archive in %s", args.directory)


def store_resources(args):
    from nbank.core import deposit
    log.debug("version: %s", nbank.__version__)
    log.debug("run time: %s", datetime.datetime.now())
    if args.read_stdin:
        args.file.extend(l.strip() for l in sys.stdin)
    try:
        deposit(args.directory, args.file, dtype=args.dtype, hash=args.hash, auto_id=args.auto_id,
                auth=args.auth, stdout=args.json_out, **args.metadata)
    except rq.exceptions.HTTPError as e:
        # bad request means the domain name is taken or badly formed
        if e.response.status_code == 400:
            data = e.response.json()
            for k, v in data.items():
                for vv in v:
                    log.error("   error: %s", vv)
        else:
            raise e


def find_resources(args):
    from nbank.core import locate
    import posixpath as pp
    try:
        from urllib.parse import urlparse
    except ImportError:
        from urlparse import urlparse
    for id in args.id:
        pr = urlparse(id)
        if pr.scheme and pr.netloc:
            # full identifier
            url, sid = pp.split(id)
        elif args.registry_url is None:
            print("%-25s [no registry to resolve short identifier]" % id)
            continue
        else:
            url = args.registry_url
            sid = id
        for loc in locate(url, sid):
            print("%-25s\t%s" % (sid, loc))


def list_datatypes(args):
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", nbank.env_registry)
        return
    for dtype in registry.get_datatypes(args.registry_url):
        print("%(name)-25s\t(%(content_type)s)" % dtype)


def add_datatype(args):
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", nbank.env_registry)
        return
    data = registry.add_datatype(args.registry_url, args.dtype_name, args.content_type, auth=args.auth)
    print("added datatype %(name)s (content-type: %(content_type)s)" % data)


# Variables:
# End:
