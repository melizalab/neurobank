# -*- mode: python -*-
"""Script entry points for neurobank

Copyright (C) 2013-2024 Dan Meliza <dan@meliza.org>
Created Tue Nov 26 22:48:58 2013
"""

import argparse
import concurrent.futures
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
            raise ValueError(f"-k {arg} argument badly formed; needs key=value")
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
        f"Default is to use the environment variable '{registry._env_registry}'",
        default=registry.default_registry(),
    )
    p.add_argument(
        "-a",
        dest="auth",
        help="username:password to authenticate with registry. "
        "If not supplied, will attempt to use .netrc file",
        type=userpwd,
        default=httpx.NetRCAuth(None),
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
    pp.add_argument(
        "-L",
        "--link",
        type=Path,
        help="generate symbolic link to the resource in DIR (local only)",
        metavar="DIR",
    )
    pp.add_argument(
        "-0",
        "--print0",
        help="print paths to stdout separated by null, for piping to xargs -0 (local only)",
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
    pp.add_argument("-n", "--archive", help="filter results by archive name")
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
        "fetch",
        help="fetch downloadable resources from the registry server",
    )
    pp.set_defaults(func=fetch_resources)
    pp.add_argument("-f", "--force", help="overwrite target file", action="store_true")
    pp.add_argument(
        "-d",
        "--dest",
        type=Path,
        help="path where the downloaded resources should be stored. Default is the current directory.",
    )
    pp.add_argument(
        "-e", "--extension", help="add an extension to downloaded file names"
    )
    pp.add_argument(
        "ids",
        nargs="+",
        help="identifier(s) of the resource(s) to fetch",
    )

    pp = sub.add_parser("dtype", help="list and add data types")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser("list", help="list datatypes")
    pp.set_defaults(func=list_datatypes)

    pp = ppsub.add_parser("add", help="add datatype")
    pp.add_argument("dtype_name", help="a unique name for the data type")
    pp.add_argument("content_type", help="the MIME content-type for the data type")
    pp.set_defaults(func=add_datatype)

    pp = sub.add_parser("archive", help="list and manipulate archives")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser("list", help="list archives")
    pp.set_defaults(func=list_archives)
    pp.add_argument("--scheme", help="filter archive list by scheme")
    pp.add_argument("-n", "--name", help="filter archive list by name")

    pp = ppsub.add_parser("check", help="verify integrity of an archive")
    pp.set_defaults(func=check_archive)
    pp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show results for all resources, not just errors",
    )
    pp.add_argument("path", type=Path, help="path of the archive to check")

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
            registry.log_error(e)
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
        registry.log_error(e)
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
            **args.metadata,
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
                print(f"{id:<20} [no registry to resolve short identifier]")
                continue
            url, params = registry.get_locations(base, id)
            try:
                locations = util.query_registry_paginated(session, url, params)
                for loc in locations:
                    resource = util.parse_location(loc)
                    if resource is None:
                        pass
                    elif args.link is not None:
                        try:
                            linkpath = resource.link(args.link)
                            print(f"{id:<20}\t-> {linkpath}")
                            break
                        except AttributeError:
                            log.info("%s doesn't support linking", resource)
                    elif args.print0:
                        try:
                            print(str(resource.path), end="\0")
                        except AttributeError:
                            log.info("%s isn't local, skipping", resource)
                    else:
                        print(f"{id:<20}\t{resource}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    print(f"{id:<20}\t(not found)")
                else:
                    registry.log_error(e)
                continue


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
        kk = f"metadata__{k}"
        params[kk] = v
    for k, v in args.metadata_neq.items():
        kk = f"metadata__{k}__neq"
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
    # missing ids just get skipped by the server, so we track which have not
    # been returned
    results = {id: {"id": id, "error": "not found"} for id in args.id}
    for result in core.describe_many(args.registry_url, *args.id):
        results[result["name"]] = result
    for _, result in results.items():
        json.dump(result, fp=sys.stdout, indent=2)
        sys.stdout.write("\n")


def set_resource_metadata(args):
    for key in args.metadata_remove:
        args.metadata[key] = None
    for result in core.update(
        args.registry_url, *args.id, auth=args.auth, **args.metadata
    ):
        json.dump(result, fp=sys.stdout, indent=2)
        sys.stdout.write("\n")


def fetch_resources(args):
    dest = args.dest or Path()
    to_fetch = set(args.ids)
    url, query = registry.get_locations_bulk(args.registry_url, to_fetch)
    with httpx.Client() as session, concurrent.futures.ThreadPoolExecutor() as executor:
        session.auth = core.make_auth(args.auth)
        response = tuple(util.query_registry_bulk(session, url, query))
        to_fetch -= {resource["name"] for resource in response}
        future_to_name = {
            executor.submit(
                util.fetch_resource,
                session,
                resource["locations"],
                (dest / resource["name"]),
                extension=args.extension,
                force=args.force,
            ): resource["name"]
            for resource in response
        }
        for future in concurrent.futures.as_completed(future_to_name):
            resource_id = future_to_name[future]
            print(f"{resource_id:<20}\t-> {future.result()}")
    for resource_id in to_fetch:
        print(f"{resource_id:<20}\t-> (no locations found)")


def list_datatypes(args):
    url, params = registry.get_datatypes(args.registry_url)
    for dtype in util.query_registry_paginated(httpx, url, params):
        print(f"{dtype['name']:<25}\t({dtype['content_type']})")


def add_datatype(args):
    url, params = registry.add_datatype(
        args.registry_url, args.dtype_name, args.content_type
    )
    resp = httpx.post(url, json=params, auth=args.auth)
    resp.raise_for_status()
    data = resp.json()
    log.info(f"added datatype {data['name']} (content-type: {data['content_type']})")


def list_archives(args):
    # parse commandline args to query dict
    argmap = [
        ("name", "name"),
        ("scheme", "scheme"),
    ]
    params = {
        paramname: getattr(args, argname)
        for (paramname, argname) in argmap
        if getattr(args, argname) is not None
    }
    url, params = registry.get_archives(args.registry_url, **params)
    for arch in util.query_registry_paginated(httpx, url, params):
        if arch["scheme"] == "neurobank":
            print(f"{arch['name']:<25}\t{arch['root']}")
        else:
            url = urlunparse((arch["scheme"], arch["root"], "", "", "", ""))
            print(f"{arch['name']:<25}\t{url}")


def check_archive(args):
    """Verify the integrity of an archive.

    - every file is readable
    - every file's hash and name matches a record in the registry
    - every record in the registry is matched with a file

    TODO support non-neurobank archives
    """
    import os

    try:
        archive_cfg = archive.get_config(args.path)
    except FileNotFoundError:
        log.error(f"error: {args.path} is not a valid neurobank archive")
        return
    archive_path = archive_cfg["path"]  # this will resolve the path
    log.info("archive: %s", archive_path)
    registry_url = archive_cfg["registry"]
    log.info("   registry: %s", registry_url)
    # retrieve a list of all the resources that should be in the archive
    with httpx.Client(auth=args.auth) as session:
        # check that archive exists for this path
        url, params = registry.find_archive_by_path(registry_url, archive_path)
        try:
            archive_name = util.query_registry_first(session, url, params)["name"]
        except TypeError as err:
            raise RuntimeError(
                f"archive '{archive_path}' not in registry. did it move?"
            ) from err
        log.info("retrieving resources in %s", archive_name)
        url, _ = registry.find_resource(registry_url)
        resource_names = {
            item["name"]: item["sha1"]
            for item in util.query_registry_paginated(
                session, url, {"location": archive_name}
            )
        }
        n_total = len(resource_names)
        n_ok = 0
        n_extra = 0
        log.info("verifying resources:")
        for resource_file in archive.iter_resources(archive_path):
            resource_name = resource_file.stem
            try:
                sha1 = resource_names.pop(resource_name)
            except KeyError:
                log.error(" - %s: FAILED to find in the registry!", resource_file)
                n_extra += 1
                continue
            msg = f" - {resource_name} : {resource_file}"
            if not os.access(resource_file, os.R_OK):
                log.error("%s - FAILED to read!", msg)
            elif util.hash(resource_file) != sha1:
                log.error("%s - FAILED to match hash!", msg)
            else:
                if args.verbose:
                    log.info("%s - OK", msg)
                n_ok += 1
        for resource_name in resource_names:
            log.error(" - %s: FAILED to find in the archive!", resource_name)
        log.info(
            "\nResources in registry: %d; missing from archive: %d; missing from registry: %d; read/verify errors: %d",
            n_total,
            len(resource_names),
            n_extra,
            n_total - n_ok,
        )


def verify_file_hash(args):
    from nbank.util import id_from_fname

    for path in args.files:
        if not path.exists():
            print(f"{path}: no such file or directory")
            continue
        test_id = id_from_fname(path)
        try:
            if core.verify(args.registry_url, path, id=test_id):
                print(f"{path}: OK")
            else:
                print(f"{path}: FAILED to match record for {test_id}")
        except ValueError:
            i = 0
            for resource in core.verify(args.registry_url, path):
                print(f"{path}: matches registry resource {resource['name']}")
                i += 1
            if i == 0:
                print(f"{path}: no matches in registry")


# Variables:
# End:
