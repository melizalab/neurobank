# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Script entry points for neurobank

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Tue Nov 26 22:48:58 2013
"""
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlunparse

import httpx

from nbank import __version__, archive, core, registry, util

log = logging.getLogger("nbank")  # root logger


def setup_log(log, debug=False):
    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    loglevel = logging.DEBUG if debug else logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)
    ch.setFormatter(formatter)
    log.addHandler(ch)


def log_error(err):
    """Writes error message from server to log. Reraises errors where code != 400"""
    if err.response.status_code == 400:
        data = err.response.json()
        for k, v in data.items():
            for vv in v:
                log.error("   registry error: %s: %s", k, vv)
    elif err.response.status_code == 415:
        log.error("    registry error: %s", err.response.reason)
    else:
        raise err


def userpwd(arg):
    """If arg is of the form username:password, returns them as a tuple. Otherwise None."""
    ret = arg.split(":")
    return tuple(ret) if len(ret) == 2 else None


def octalint(arg):
    """Parse arg as an octal literal"""
    return int(arg, base=8)


class ParseKeyVal(argparse.Action):
    def parse_value(self, value):
        import ast

        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value

    def __call__(self, parser, namespace, arg, option_string=None):
        kv = getattr(namespace, self.dest)
        if kv is None:
            kv = dict()
        if not arg.count("=") == 1:
            raise ValueError("-k %s argument badly formed; needs key=value" % arg)
        else:
            key, val = arg.split("=")
            kv[key] = self.parse_value(val)
        setattr(namespace, self.dest, kv)


def main(argv=None):

    p = argparse.ArgumentParser(description="manage source files and collected data")
    p.add_argument(
        "-v", "--version", action="version", version="%(prog)s " + __version__
    )
    p.add_argument(
        "-r",
        dest="registry_url",
        help="URL of the registry service. "
        "Default is to use the environment variable '%s'" % registry._env_registry,
        default=registry.default_registry(),
    )
    p.add_argument(
        "-a",
        dest="auth",
        help="username:password to authenticate with registry. "
        "If not supplied, will attempt to use .netrc file",
        type=userpwd,
        default=None,
    )
    p.add_argument("--debug", help="show verbose log messages", action="store_true")

    sub = p.add_subparsers(title="subcommands")

    pp = sub.add_parser("registry-info", help="get information about the registry")
    pp.set_defaults(func=registry_info)

    pp = sub.add_parser("init", help="initialize a data archive")
    pp.set_defaults(func=init_archive)
    pp.add_argument(
        "directory",
        type=Path,
        help="path of the directory for the archive. "
        "The directory should be empty or not exist. ",
    )
    pp.add_argument(
        "-n",
        dest="name",
        help="name to give the archive in the registry. "
        "The default is to use the directory name of the archive.",
        default=None,
    )
    pp.add_argument(
        "-u",
        dest="umask",
        help="umask for newly created files in archive, "
        "as an octal. The default is %(default)03o.",
        type=octalint,
        default=archive._default_umask,
    )

    pp = sub.add_parser("deposit", help="deposit resource(s)")
    pp.set_defaults(func=store_resources)
    pp.add_argument("directory", type=Path, help="path of the archive ")
    pp.add_argument(
        "-d", "--dtype", help="specify the datatype for the deposited resources"
    )
    pp.add_argument(
        "-H",
        "--hash",
        action="store_true",
        help="calculate a SHA1 hash of each file and store in the registry",
    )
    pp.add_argument(
        "-A",
        "--auto-id",
        action="store_true",
        help="ask the registry to generate an id for each resource",
    )
    pp.add_argument(
        "-k",
        help="specify metadata field (use multiple -k for multiple values)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata",
    )
    pp.add_argument(
        "-j",
        "--json-out",
        action="store_true",
        help="output each deposited file to stdout as line-deliminated JSON",
    )
    pp.add_argument(
        "-@",
        dest="read_stdin",
        action="store_true",
        help="read additional file names from stdin",
    )
    pp.add_argument(
        "file", nargs="+", type=Path, help="path of file(s) to add to the repository"
    )

    pp = sub.add_parser("locate", help="locate local resource(s)")
    pp.set_defaults(func=locate_resources)
    pp.add_argument("-R", "--remote", action="store_true", help="show remote locations")
    pp.add_argument(
        "-L",
        "--link",
        type=Path,
        help="generate symbolic link to the resource in DIR",
        metavar="DIR",
    )
    pp.add_argument(
        "-0",
        "--print0",
        help="print paths to stdout separated by null, for piping to xargs -0",
        action="store_true",
    )
    pp.add_argument("id", help="the identifier of the resource", nargs="+")

    pp = sub.add_parser("search", help="search for resource(s)")
    pp.set_defaults(func=search_resources)
    pp.add_argument(
        "-j",
        "--json-out",
        help="output full record as json (otherwise just name)",
        action="store_true",
    )
    pp.add_argument("-d", "--dtype", help="filter results by dtype")
    pp.add_argument("-H", "--hash", help="filter results by hash")
    pp.add_argument("-n", "--archive", help="filter results by archive location")
    pp.add_argument(
        "-k",
        help="filter by metadata field (use multiple -k for multiple values)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata",
    )
    pp.add_argument(
        "-K",
        help="exclude by metadata field (use multiple -K for multiple values)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata_neq",
    )
    pp.add_argument("name", help="resource name or fragment to search by", nargs="?")

    pp = sub.add_parser("info", help="get info from registry about resource(s)")
    pp.set_defaults(func=get_resource_info)
    pp.add_argument("id", nargs="+", help="the identifier of the resource")

    pp = sub.add_parser(
        "verify",
        help="compute sha1 hash and check that it matches a record in the database",
    )
    pp.set_defaults(func=verify_file_hash)
    pp.add_argument(
        "files", nargs="+", type=Path, help="the files or directories to verify"
    )

    pp = sub.add_parser(
        "modify", help="update values in resource metadata of resource(s)"
    )
    pp.set_defaults(func=set_resource_metadata)
    pp.add_argument(
        "-k",
        help="set metadata key=value, replacing any previous "
        "value for this key (use multiple -k for multiple fields)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata",
    )
    pp.add_argument(
        "-K",
        help="delete metadata field",
        action="append",
        default=[],
        metavar="KEY",
        dest="metadata_remove",
    )
    pp.add_argument("id", nargs="+", help="identifier(s) of the resource(s)")

    pp = sub.add_parser(
        "fetch", help="fetch downloadable resources from the registry server"
    )
    pp.set_defaults(func=fetch_resource)
    pp.add_argument("-f", "--force", help="overwrite target file", action="store_true")
    pp.add_argument("id", help="identifier of the resource")
    pp.add_argument(
        "target",
        type=Path,
        help="path where the downloaded resource should be stored. If this is a directory, "
        "the target file is named after the resource (without any extension)",
    )

    pp = sub.add_parser("dtype", help="list and add data types")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser("list", help="list datatypes")
    pp.set_defaults(func=list_datatypes)

    pp = ppsub.add_parser("add", help="add datatype")
    pp.add_argument("dtype_name", help="a unique name for the data type")
    pp.add_argument("content_type", help="the MIME content-type for the data type")
    pp.set_defaults(func=add_datatype)

    pp = sub.add_parser("archives", help="list available archives (archives)")
    pp.set_defaults(func=list_archives)

    args = p.parse_args(argv)

    if not hasattr(args, "func"):
        p.print_usage()
        return 0

    setup_log(log, args.debug)
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())

    # most commands requre a registry, so check it here once
    if args.registry_url is None and args.func not in (
        store_resources,
        locate_resources,
    ):
        log.error(
            "error: supply a registry url with '-r' or %s environment variable",
            registry._env_registry,
        )
        return

    # some of the error handling is common; sub-funcs should only catch specific errors
    try:
        args.func(args)
    except httpx.RequestError:
        log.error("registry error: unable to contact server")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            log.error(
                "authentication error: Authenticate with '-a username:password' or .netrc file."
            )
            log.error(
                "                      Or, you may not have permission for this operation."
            )
        else:
            log_error(e)
    except KeyboardInterrupt:
        pass


def registry_info(args):
    log.info("registry info:")
    log.info("  - address: %s", args.registry_url)
    url, params = registry.get_info(args.registry_url)
    for k, v in util.query_registry(httpx, url, params, auth=args.auth).items():
        log.info("  - %s: %s", k, v)


def init_archive(args):
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())
    args.directory = args.directory.resolve()
    if args.name is None:
        args.name = args.directory.name

    url, params = registry.add_archive(
        args.registry_url,
        args.name,
        registry._neurobank_scheme,
        args.directory,
    )
    try:
        r = httpx.post(url, json=params, auth=args.auth)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        log_error(e)
    else:
        log.info("registered '%s' as archive '%s'", args.directory, args.name)
        archive.create(args.directory, args.registry_url, args.umask)
        log.info("initialized neurobank archive in %s", args.directory)


def store_resources(args):
    if args.read_stdin:
        args.file.extend(line.strip() for line in sys.stdin)
    try:
        for res in core.deposit(
            args.directory,
            args.file,
            dtype=args.dtype,
            hash=args.hash,
            auto_id=args.auto_id,
            auth=args.auth,
            **args.metadata
        ):
            if args.json_out:
                json.dump(res, fp=sys.stdout)
                sys.stdout.write("\n")
    except ValueError as e:
        log.error("error: %s", e)


def locate_resources(args):
    # This subcommand can handle IDs or full neurobank URLs
    with httpx.Client() as session:
        for id in args.id:
            try:
                base, id = registry.parse_resource_url(id)
            except ValueError:
                base = args.registry_url
            if base is None:
                print("%-25s [no registry to resolve short identifier]" % id)
                continue
            url, params = registry.get_locations(base, id)
            for loc in util.query_registry_paginated(session, url, params):
                # this will return a Path for local files and a str for URLs
                partial = util.parse_location(loc)
                if args.remote and isinstance(partial, str):
                    print("%-20s\t%s" % (id, partial))
                elif not args.remote and isinstance(partial, Path):
                    try:
                        path = archive.resolve_extension(partial)
                    except FileNotFoundError:
                        continue
                    if args.link is not None:
                        linkpath = args.link / path.name
                        print("%-20s\t-> %s" % (id, linkpath))
                        linkpath.symlink_to(path)
                    elif args.print0:
                        print(str(path), end="\0")
                    else:
                        print("%-20s\t%s" % (id, path))


def search_resources(args):
    # parse commandline args to query dict
    argmap = [
        ("name", "name"),
        ("dtype", "dtype"),
        ("sha1", "hash"),
        ("location", "archive"),
    ]
    params = {
        paramname: getattr(args, argname)
        for (paramname, argname) in argmap
        if getattr(args, argname) is not None
    }
    for k, v in args.metadata.items():
        kk = "metadata__%s" % k
        params[kk] = v
    for k, v in args.metadata_neq.items():
        kk = "metadata__%s__neq" % k
        params[kk] = v
    if len(params) == 0:
        log.error("nbank search: error: at least one filter parameter is required")
        return
    for d in core.search(args.registry_url, **params):
        if args.json_out:
            json.dump(d, fp=sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(d["name"])


def get_resource_info(args):
    # we don't use core.describe to allow request pooling
    with httpx.Client() as session:
        for id in args.id:
            url, params = registry.get_resource(args.registry_url, id)
            data = util.query_registry(session, url, params)
            if data is None:
                data = {"id": id, "error": "not found"}
            json.dump(data, fp=sys.stdout, indent=2)
            sys.stdout.write("\n")


def set_resource_metadata(args):
    for key in args.metadata_remove:
        args.metadata[key] = None
    for result in core.update(
        args.registry_url, *args.id, auth=args.auth, **args.metadata
    ):
        json.dump(result, fp=sys.stdout, indent=2)
        sys.stdout.write("\n")


def fetch_resource(args):
    if args.target.is_dir():
        target = args.target / args.id
    else:
        target = args.target
    if target.exists():
        if args.force:
            log.debug("removing target file %s", target)
            target.unlink()
        else:
            log.error("nbank fetch: error: the target file %s exists already", target)
            return
    try:
        core.fetch(args.registry_url, args.id, target)
    except ValueError as e:
        log.error(e)
        return


def list_datatypes(args):
    url, params = registry.get_datatypes(args.registry_url)
    for dtype in util.query_registry_paginated(httpx, url, params):
        print("%(name)-25s\t(%(content_type)s)" % dtype)


def add_datatype(args):
    url, params = registry.add_datatype(
        args.registry_url, args.dtype_name, args.content_type
    )
    data = httpx.post(url, json=params, auth=args.auth)
    log.info("added datatype %(name)s (content-type: %(content_type)s)" % data.json())


def list_archives(args):
    url, params = registry.get_archives(args.registry_url)
    for arch in util.query_registry_paginated(httpx, url, params):
        if arch["scheme"] == "neurobank":
            print("%(name)-25s\t%(root)s" % arch)
        else:
            url = urlunparse(arch["scheme"], arch["root"], "", "", "", "")
            print("%-25s\t%s" % (arch["name"], url))


def verify_file_hash(args):
    from nbank.util import id_from_fname

    for path in args.files:
        if not path.exists():
            print("%s: no such file or directory" % path)
            continue
        test_id = id_from_fname(path)
        try:
            if core.verify(args.registry_url, path, id=test_id):
                print("%s: OK" % path)
            else:
                print("%s: FAILED to match record for %s" % (path, test_id))
        except ValueError:
            i = 0
            for resource in core.verify(args.registry_url, path):
                print("%s: matches %s" % (path, resource["name"]))
                i += 1
            if i == 0:
                print("%s: no matches in registry" % path)


# Variables:
# End:
