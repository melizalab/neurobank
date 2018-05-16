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
import argparse
import datetime
import logging
import requests as rq

try:
    from urllib.parse import urlunparse
except ImportError:
    from urlparse import urlunparse

from nbank import __version__
from nbank import core, archive, registry

log = logging.getLogger('nbank')   # root logger

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
                   version="%(prog)s " + __version__)
    p.add_argument('-r', dest='registry_url', help="URL of the registry service. "
                    "Default is to use the environment variable '%s'" % core.env_registry,
                    default=core.default_registry())
    p.add_argument('-a', dest='auth', help="username:password to authenticate with registry. "
                   "If not supplied, will attempt to use .netrc file",
                   type=userpwd, default=None)
    p.add_argument('--debug', help="show verbose log messages", action="store_true")

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
    pp.add_argument('-k', help="specify metadata field (use multiple -k for multiple values)",
                    action=ParseKeyVal, default=dict(), metavar="KEY=VALUE", dest='metadata')
    pp.add_argument('-j', "--json-out", action="store_true",
                    help="output each deposited file to stdout as line-deliminated JSON")
    pp.add_argument('-@', dest="read_stdin", action='store_true',
                       help="read additional file names from stdin")
    pp.add_argument('file', nargs='+',
                       help='path of file(s) to add to the repository')

    pp = sub.add_parser('locate', help="locate resource(s)")
    pp.set_defaults(func=locate_resources)
    pp.add_argument("-l", "--local-only", help="only show local resources that exist",
                    action="store_true")
    pp.add_argument("id", help="the identifier of the resource", nargs='+')

    pp = sub.add_parser('search', help="search for resource(s)")
    pp.set_defaults(func=search_resources)
    pp.add_argument("query", help="resource name or fragment to search by")

    pp = sub.add_parser('info', help="get info from registry about resource(s)")
    pp.set_defaults(func=get_resource_info)
    pp.add_argument("id", nargs='+', help="the identifier of the resource")

    pp = sub.add_parser('dtype', help='list and add data types')
    ppsub = pp.add_subparsers(title='subcommands')

    pp = ppsub.add_parser('list', help='list datatypes')
    pp.set_defaults(func=list_datatypes)

    pp = ppsub.add_parser('add', help='add datatype')
    pp.add_argument("dtype_name", help="a unique name for the data type")
    pp.add_argument("content_type", help="the MIME content-type for the data type")
    pp.set_defaults(func=add_datatype)

    pp = sub.add_parser('archives', help='list available archives (archives)')
    pp.set_defaults(func=list_archives)

    args = p.parse_args(argv)

    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    loglevel = logging.DEBUG if args.debug else logging.INFO
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
            log.error("authentication error: Authenticate with '-a username:password' or .netrc file.")
            log.error("                      Or, you may not have permission for this operation.")
        else:
            log.error("internal registry error:")
            raise e
    except RuntimeError as e:
        log.error("MAJOR ERROR: archive may have become corrupted")
        raise e


def init_archive(args):
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())
    args.directory = os.path.abspath(args.directory)
    if args.name is None:
        args.name = os.path.basename(args.directory)
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", core.env_registry)
        return

    try:
        registry.add_archive(args.registry_url, args.name, registry._neurobank_scheme,
                            args.directory, args.auth)
    except rq.exceptions.HTTPError as e:
        registry.log_error(e)
    else:
        log.info("registered '%s' as archive '%s'", args.directory, args.name)
        archive.create(args.directory, args.registry_url, args.umask)
        log.info("initialized neurobank archive in %s", args.directory)


def store_resources(args):
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())
    if args.read_stdin:
        args.file.extend(l.strip() for l in sys.stdin)
    try:
        for res in core.deposit(args.directory, args.file, dtype=args.dtype, hash=args.hash,
                                auto_id=args.auto_id, auth=args.auth, **args.metadata):
            if args.json_out:
                json.dump(res, fp=sys.stdout)
                sys.stdout.write("\n")
    except ValueError as e:
        log.error("error: %s", e)
    except rq.exceptions.HTTPError as e:
        registry.log_error(e)


def locate_resources(args):
    for id in args.id:
        base, sid = registry.parse_resource_id(args.registry_url, id)
        if base is None:
            print("%-25s [no registry to resolve short identifier]" % id)
            continue
        for loc in registry.get_locations(base, sid):
            path = core.get_path_or_url(loc)
            if args.local_only:
                path = archive.find_resource(path)
            if path is not None:
                print("%-20s\t%s" % (sid, path))


def search_resources(args):
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", core.env_registry)
        return
    for d in registry.find_resource_by_name(args.registry_url, args.query):
        print(d["name"])


def get_resource_info(args):
    for id in args.id:
        data = core.describe(id, args.registry_url)
        if data is None:
            data = {"id": id, "error": "not found"}
        json.dump(data, fp=sys.stdout, indent=2)


def list_datatypes(args):
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", core.env_registry)
        return
    for dtype in registry.get_datatypes(args.registry_url):
        print("%(name)-25s\t(%(content_type)s)" % dtype)


def add_datatype(args):
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", core.env_registry)
        return
    try:
        data = registry.add_datatype(args.registry_url, args.dtype_name, args.content_type, auth=args.auth)
        print("added datatype %(name)s (content-type: %(content_type)s)" % data)
    except rq.exceptions.HTTPError as e:
        registry.log_error(e)


def list_archives(args):
    if args.registry_url is None:
        log.error("error: supply a registry url with '-r' or %s environment variable", core.env_registry)
        return
    for arch in registry.get_archives(args.registry_url):
        if arch["scheme"] == "neurobank":
            print("%(name)-25s\t%(root)s" % arch)
        else:
            url = urlunparse(arch["scheme"], arch["root"], '', '', '', '')
            print("%-25s\t%s" % (arch["name"], url))


# Variables:
# End:
