# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Script entry points for neurobank"""

import argparse
import codecs
import concurrent.futures
import datetime
import json
import logging
import os
import sys
import tarfile
import time
from collections import Counter, deque
from collections.abc import Iterator
from netrc import NetrcParseError
from pathlib import Path, PurePosixPath
from urllib.parse import urlunparse

import httpx

from nbank import __version__, archive, check, core, readers, registry, transfer, util

log = logging.getLogger("nbank")  # root logger


def utf8_console():
    """Writes standard output and error as UTF-8 when they use another encoding.

    Windows writes redirected output in its ANSI code page, which can't hold every
    name or note in the registry; characters a stream can't take are replaced.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if encoding is None or not hasattr(stream, "reconfigure"):
            continue
        if codecs.lookup(encoding).name != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def setup_log(log, debug=False):
    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    loglevel = logging.DEBUG if debug else logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)
    ch.setFormatter(formatter)
    log.addHandler(ch)


def userpwd(arg):
    """Parses arg of the form username:password into a tuple.

    The password can contain colons. Raises ArgumentTypeError if arg has no colon.
    """
    user, sep, password = arg.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError(
            f"'{arg}' is not of the form username:password"
        )
    return (user, password)


def octalint(arg):
    """Parse arg as an octal literal"""
    return int(arg, base=8)


# -k values accepted for compatibility with Python literals
_KEYVAL_ALIASES = {"True": True, "False": False, "None": None}


def _reject_constant(name):
    # NaN and Infinity aren't JSON, and the registry can't store them
    raise ValueError(f"{name} is not a JSON value")


class ParseKeyVal(argparse.Action):
    def parse_value(self, value):
        """Returns value parsed as JSON if it is JSON, and otherwise as a string.

        Raises ValueError for a value that looks like a JSON list or object but
        doesn't parse, so a typo isn't stored as a string.
        """
        if value in _KEYVAL_ALIASES:
            return _KEYVAL_ALIASES[value]
        try:
            return json.loads(value, parse_constant=_reject_constant)
        except ValueError as err:
            if value.lstrip().startswith(("[", "{")):
                raise ValueError(f"{value} is not a valid JSON list or object") from err
            return value

    def __call__(self, parser, namespace, arg, option_string=None):
        kv = getattr(namespace, self.dest)
        if kv is None:
            kv = dict()
        if not arg.count("=") == 1:
            raise ValueError(f"-k {arg} argument badly formed; needs key=value")
        else:
            key, val = arg.split("=")
            if key in registry.core_fields:
                parser.error(
                    f"{option_string} {key}: {key} is not metadata; use --{key}"
                )
            try:
                kv[key] = self.parse_value(val)
            except ValueError as err:
                parser.error(f"{option_string} {key}: {err}")
        setattr(namespace, self.dest, kv)


def metadata_key(arg):
    """Parse arg as a metadata key, which can't be the name of a resource field"""
    if arg in registry.core_fields:
        raise argparse.ArgumentTypeError(f"{arg} is not metadata; use --{arg}")
    return arg


# where archives are mounted on this host, if not at their registered roots
_env_alt_base = "NBANK_ALT_BASE"


def add_alt_base_arg(pp):
    """Adds --alt-base to a subcommand parser that reads from archives."""
    pp.add_argument(
        "--alt-base",
        type=Path,
        metavar="DIR",
        help="the directory that holds the archives on this host, if they're not at "
        "their registered roots (e.g. a share mounted from another host): "
        "/home/data/starlings is read as DIR/starlings. Default is to use the "
        f"environment variable '{_env_alt_base}'",
    )


def add_core_field_args(pp, action: str):
    """Adds --experimenter and --animal to a subcommand parser.

    action is "set", "update", or "search".
    """
    help = {
        "set": (
            "the person who collected or produced the data (a registry username)",
            "the full identifier of the animal the data came from",
        ),
        "update": (
            "set the experimenter (a registry username; empty to clear)",
            "set the animal, by its full identifier (empty to clear)",
        ),
        "search": (
            "filter results by experimenter (username)",
            "filter results by animal (the start of its identifier is enough)",
        ),
    }[action]
    pp.add_argument("--experimenter", metavar="USER", help=help[0])
    pp.add_argument("--animal", metavar="ID", help=help[1])


def core_field_args(args) -> dict:
    """Returns the values of --experimenter and --animal that were given"""
    return {
        field: value
        for field in registry.core_fields
        if (value := getattr(args, field)) is not None
    }


def add_check_options(pp):
    """Adds the options for checking archives to a subcommand parser."""
    pp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show results for all resources, not just errors",
    )
    pp.add_argument(
        "--fix",
        action="store_true",
        help="fix ownership and permissions (changing ownership requires root)",
    )
    pp.add_argument(
        "--no-hash",
        action="store_true",
        help="don't verify file hashes (much faster for large archives)",
    )


def make_parser() -> argparse.ArgumentParser:
    """Returns the parser for the nbank command line."""
    p = argparse.ArgumentParser(
        prog="nbank", description="manage source files and collected data"
    )
    p.add_argument(
        "-v", "--version", action="version", version="%(prog)s " + __version__
    )
    p.add_argument(
        "-r",
        dest="registry_url",
        help="URL of the registry service. "
        f"Default is to use the environment variable '{registry._env_registry}'",
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

    pp = sub.add_parser("registry", help="registry information")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser(
        "info", help="show the registry's version, and who it sees you as"
    )
    pp.set_defaults(func=registry_info)

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
        help="give each resource a random id instead of its file name",
    )
    pp.add_argument(
        "-y",
        "--dry-run",
        action="store_true",
        help="run all pre-flight checks, including asking the registry whether it "
        "would accept each resource, but don't register or move any files",
    )
    pp.add_argument(
        "-k",
        help="specify metadata field (use multiple -k for multiple values;"
        " VALUE is read as JSON if it parses, otherwise as a string)",
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
    add_core_field_args(pp, "set")
    pp.add_argument(
        "--no-file-metadata",
        dest="read_metadata",
        action="store_false",
        help="don't check the files or read their metadata with the dtype's reader",
    )
    pp.add_argument(
        "--zip-directories",
        action="store_true",
        help="deposit each directory as a zip file of its contents (the directory "
        "is left in place)",
    )
    pp.add_argument(
        "--override-file-metadata",
        action="store_true",
        help="where the given metadata differs from a file's, use the given values",
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
    add_alt_base_arg(pp)
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
    pp.add_argument(
        "-H", "--hash", help="filter results by hash (full, or any part of one)"
    )
    pp.add_argument(
        "-n", "--archive", help="filter results by the exact name of an archive"
    )
    pp.add_argument(
        "-s",
        "--scheme",
        help="filter results by the scheme of any of their locations "
        "(e.g., neurobank, tape, https)",
    )
    pp.add_argument(
        "-k",
        help="filter by metadata field (use multiple -k for multiple values;"
        " VALUE is read as JSON if it parses, otherwise as a string)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata",
    )
    pp.add_argument(
        "-K",
        help="exclude by metadata field (use multiple -K for multiple values;"
        " VALUE is read as JSON if it parses, otherwise as a string)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata_neq",
    )
    add_core_field_args(pp, "search")
    group = pp.add_mutually_exclusive_group()
    group.add_argument(
        "--superseded",
        action="store_const",
        const=True,
        default=False,
        help="find only superseded resources (by default they're left out)",
    )
    group.add_argument(
        "--all",
        action="store_const",
        const=None,
        dest="superseded",
        help="find superseded resources as well as current ones",
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
        "--metadata",
        action="store_true",
        help="also read each file with its dtype's metadata reader and check what "
        "it reads against the registry (--debug shows it)",
    )
    pp.add_argument("files", nargs="+", type=Path, help="the files to verify")

    pp = sub.add_parser(
        "modify", help="update values in resource metadata of resource(s)"
    )
    pp.set_defaults(func=set_resource_metadata)
    pp.add_argument(
        "-k",
        help="set metadata key=value, replacing any previous "
        "value for this key (use multiple -k for multiple fields;"
        " VALUE is read as JSON if it parses, otherwise as a string)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata",
    )
    pp.add_argument(
        "-K",
        help="delete metadata field",
        action="append",
        type=metadata_key,
        default=[],
        metavar="KEY",
        dest="metadata_remove",
    )
    add_core_field_args(pp, "update")
    pp.add_argument("-d", "--dtype", help="change the datatype of the resource(s)")
    pp.add_argument("id", nargs="+", help="identifier(s) of the resource(s)")

    pp = sub.add_parser(
        "fetch",
        help="copy resources from an archive on this host, or download them, "
        "from the first location that works",
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
        "--verify",
        action="store_true",
        help="check each file against its registered hash, trying other locations "
        "if it doesn't match",
    )
    pp.add_argument(
        "-@",
        dest="read_stdin",
        action="store_true",
        help="read additional identifiers from stdin, one per line",
    )
    add_alt_base_arg(pp)
    pp.add_argument(
        "ids",
        nargs="*",
        help="identifier(s) of the resource(s) to fetch",
    )

    pp = sub.add_parser(
        "export",
        help="write resources from neurobank archives on this host to a tar file, "
        "zip file, or directory",
    )
    pp.set_defaults(func=export_resources)
    pp.add_argument(
        "-a",
        "--archive",
        help="only read copies in this archive",
    )
    pp.add_argument(
        "-f",
        "--from-file",
        type=Path,
        help="read identifiers from FILE, one per line ('-' for standard input)",
        metavar="FILE",
    )
    pp.add_argument(
        "--compress",
        help="compress the members of a zip file (default is to store them as-is)",
        action="store_true",
    )
    pp.add_argument(
        "--manifest",
        help=f"add the registry records of the exported resources, as "
        f"{transfer.manifest_name}",
        action="store_true",
    )
    add_alt_base_arg(pp)
    pp.add_argument(
        "out",
        type=Path,
        help="where to write the resources: a tar file (.tar) or zip file (.zip) "
        "to create, or else a directory",
    )
    pp.add_argument("ids", nargs="*", help="identifier(s) of the resource(s) to export")

    pp = sub.add_parser(
        "copy",
        help="copy resources from neurobank archives on this host into another one",
    )
    pp.set_defaults(func=copy_resources)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="check the resources against the registry without copying any files "
        "or changing the registry",
        action="store_true",
    )
    pp.add_argument(
        "-a",
        "--archive",
        help="only read copies in this archive",
    )
    pp.add_argument(
        "-f",
        "--from-file",
        type=Path,
        help="read identifiers from FILE, one per line ('-' for standard input)",
        metavar="FILE",
    )
    add_alt_base_arg(pp)
    pp.add_argument("dest", type=Path, help="path of the destination neurobank archive")
    pp.add_argument("ids", nargs="*", help="identifier(s) of the resource(s) to copy")

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

    pp = ppsub.add_parser("init", help="create a neurobank archive and register it")
    pp.set_defaults(func=init_archive)
    pp.add_argument(
        "directory",
        type=Path,
        help="path of the directory for the archive. "
        "The directory should be empty or not exist.",
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
    pp.add_argument(
        "-g",
        "--group",
        help="group for the archive. The default is your primary group.",
    )
    pp.add_argument(
        "--shared",
        action="store_true",
        help="configure archive for shared use, with users depositing under "
        "their own accounts",
    )

    pp = ppsub.add_parser("list", help="list archives")
    pp.set_defaults(func=list_archives)
    pp.add_argument("--scheme", help="filter archive list by scheme")
    pp.add_argument("-n", "--name", help="filter archive list by name")

    pp = ppsub.add_parser(
        "register",
        help="register an archive stored elsewhere, and the resources in it",
    )
    regsub = pp.add_subparsers(title="schemes")

    pp = regsub.add_parser(
        "tape",
        help="register a tar file written to tape as an archive, and the resources "
        "in it. Refuses an archive that is already registered",
    )
    pp.set_defaults(func=register_tape)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="don't make any changes to the registry",
        action="store_true",
    )
    pp.add_argument("--archive-name", "-n", help="name of the archive")
    pp.add_argument(
        "tape_name", type=str, help="name of the tape where the tar file was written"
    )
    pp.add_argument(
        "file_number",
        type=int,
        help="index of the file on the tape where the tar file was written",
    )
    pp.add_argument("tar", type=Path, help="tar file with the resources to transfer")

    pp = ppsub.add_parser(
        "prune",
        help="remove files from a neurobank archive that are stored somewhere else",
    )
    pp.set_defaults(func=prune_archive)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="don't delete any files or make any changes to the registry",
        action="store_true",
    )
    pp.add_argument("archive_name", type=str, help="name of the archive to prune")
    pp.add_argument("resources", type=Path, help="file with list of resources to prune")

    pp = ppsub.add_parser(
        "import-tar",
        help="import resources from a tar file to a neurobank archive",
    )
    pp.set_defaults(func=import_tar)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="check the resources against the registry without copying any files "
        "or changing the registry",
        action="store_true",
    )
    pp.add_argument(
        "tar",
        type=Path,
        help="tar file with the resources to import: a file, a tape device, or "
        "'-' for standard input",
    )
    pp.add_argument(
        "-f",
        "--from-file",
        type=Path,
        help="read identifiers of the resources to import from FILE, one per line "
        "('-' for standard input)",
        metavar="FILE",
    )
    pp.add_argument("dest", type=Path, help="path of the destination neurobank archive")
    pp.add_argument(
        "ids",
        nargs="*",
        help="identifier(s) of the resources to import (default: all of them)",
    )

    pp = sub.add_parser("check", help="check integrity of the registry and archives")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser(
        "registry",
        help="check the registry for resources without locations and empty archives",
    )
    pp.set_defaults(func=check_registry)

    pp = ppsub.add_parser(
        "archive",
        help="check an archive's contents against the registry, and its "
        "ownership and permissions against its policy",
    )
    pp.set_defaults(func=check_archive)
    add_check_options(pp)
    pp.add_argument(
        "archive",
        help="path of the archive to check, or its name in the registry. "
        "An existing directory is taken as a path",
    )

    pp = ppsub.add_parser(
        "all",
        help="check the registry, then every neurobank archive on this host",
    )
    pp.set_defaults(func=check_all)
    add_check_options(pp)

    pp = ppsub.add_parser(
        "superseded",
        help="check that resources aren't superseded, e.g. the inputs to an analysis",
    )
    pp.set_defaults(func=check_superseded)
    pp.add_argument(
        "-@",
        dest="read_stdin",
        action="store_true",
        help="read additional identifiers from stdin, one per line",
    )
    pp.add_argument("ids", nargs="*", help="identifier(s) of the resources")

    pp = ppsub.add_parser(
        "tar",
        help="check the resources in a tar file (or tape) against the registry",
    )
    pp.set_defaults(func=check_tar)
    pp.add_argument(
        "-a",
        "--archive",
        help="also check that every resource the registry places in this archive "
        "is in the tar file",
    )
    pp.add_argument(
        "tar",
        type=Path,
        help="the tar file: a file, a tape device, or '-' for standard input",
    )

    return p


def main(argv=None):
    p = make_parser()
    args = p.parse_args(argv)

    if not hasattr(args, "func"):
        p.print_usage()
        return 0

    utf8_console()
    setup_log(log, args.debug)
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())

    # read after parsing, so the parser (and documentation built from it) doesn't
    # hold this host's value
    if args.registry_url is None:
        args.registry_url = registry.default_registry()
    if getattr(args, "alt_base", False) is None and os.environ.get(_env_alt_base):
        args.alt_base = Path(os.environ[_env_alt_base])
    # resolved after parsing so that a missing ~/.netrc doesn't break the parser
    try:
        args.auth = core.make_auth(args.auth)
    except NetrcParseError as err:
        log.error("error: unable to use netrc file: %s", err.msg)
        return 1

    if not archive._posix and args.func in _archive_commands:
        log.error(
            "error: archives are written and checked on a POSIX host (Linux or "
            "macOS), not on Windows"
        )
        return 1

    # most commands require a registry, so check it here once
    if args.registry_url is None and args.func not in (
        store_resources,
        locate_resources,
    ):
        log.error(
            "error: supply a registry url with '-r' or %s environment variable",
            registry._env_registry,
        )
        return 1

    # some of the error handling is common; sub-funcs should only catch specific
    # errors. Commands return 1 (or None for success) and exit status follows.
    try:
        status = args.func(args)
        # buffered output reaches a closed pipe here
        sys.stdout.flush()
        return status
    except BrokenPipeError:
        # the reader stopped early (e.g. `| head`): exit as if killed by SIGPIPE
        _discard_stdout()
        return 141
    except httpx.RequestError:
        log.error("registry error: unable to contact server")
    except util.RegistryVersionError as err:
        log.error("error: %s", err)
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
        return 130
    return 1


def _discard_stdout() -> None:
    """Points standard output at the null device, so that flushing it at exit doesn't
    fail again."""
    try:
        fd = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, fd)
    os.close(devnull)


def registry_info(args):
    log.info("registry info:")
    log.info("  - address: %s", args.registry_url)
    url, params = registry.get_info(args.registry_url)
    # no version check, so this shows any registry's API version
    with util.registry_session(
        args.registry_url, auth=args.auth, check_version=False
    ) as session:
        info = util.query_registry(session, url, params)
    if info is None:
        log.error("error: %s is not a neurobank registry (no info/)", args.registry_url)
        return 1
    # registries before API 1.3 don't say who the request is from
    has_user = "user" in info
    user = info.pop("user", None)
    for k, v in info.items():
        log.info("  - %s: %s", k, v)
    if not has_user:
        return
    if user is None:
        log.info("  - user: anonymous (no credentials; use -a or a .netrc file)")
    elif user["superuser"]:
        log.info("  - user: %s (superuser)", user["username"])
    else:
        log.info("  - user: %s", user["username"])
        log.info("  - permissions: %s", ", ".join(user["permissions"]) or "none")


def init_archive(args):
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())
    args.directory = args.directory.resolve()
    if args.name is None:
        args.name = args.directory.name
    # check before registering, so a problem doesn't leave a registered archive
    if args.group is not None:
        import grp

        try:
            grp.getgrnam(args.group)
        except KeyError:
            log.error("error: group '%s' does not exist", args.group)
            return 1
    try:
        archive.verify_can_create(args.directory)
    except FileExistsError as err:
        log.error("error: %s", err)
        return 1

    url, params = registry.add_archive(
        args.registry_url,
        args.name,
        registry._neurobank_scheme,
        args.directory,
    )
    try:
        with util.registry_session(args.registry_url, auth=args.auth) as session:
            r = session.post(url, json=params)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        registry.log_error(e)
        return 1
    else:
        log.info("registered '%s' as archive '%s'", args.directory, args.name)
        archive.create(
            args.directory,
            args.registry_url,
            umask=args.umask,
            shared=args.shared,
            group=args.group,
        )
        log.info("initialized neurobank archive in %s", args.directory)


def store_resources(args):
    if args.read_stdin:
        args.file.extend(Path(name) for line in sys.stdin if (name := line.strip()))
    n_failed = 0
    try:
        for res in core.deposit(
            args.directory,
            args.file,
            dtype=args.dtype,
            hash=args.hash,
            auto_id=args.auto_id,
            auth=args.auth,
            dry_run=args.dry_run,
            skip_errors=True,
            read_metadata=args.read_metadata,
            override_file_metadata=args.override_file_metadata,
            zip_directories=args.zip_directories,
            metadata={**args.metadata, **core_field_args(args)},
        ):
            n_failed += "error" in res
            if args.json_out:
                json.dump(res, fp=sys.stdout, cls=util.JSONEncoder)
                sys.stdout.write("\n")
    except (ValueError, OSError, RuntimeError) as e:
        log.error("error: %s", e)
        return 1
    if n_failed:
        log.error("files that could not be deposited: %d", n_failed)
        return 1


def locate_resources(args):
    """Returns 1 if any id can't be resolved to a location reachable from this host."""
    # This subcommand can handle IDs or full neurobank URLs
    n_failed = 0
    with util.registry_session() as session:
        for id in args.id:
            try:
                base, id = registry.parse_resource_url(id)
            except ValueError:
                base = args.registry_url
            if base is None:
                print(f"{id:<20} [no registry to resolve short identifier]")
                n_failed += 1
                continue
            util.check_registry_version(session, base)
            url, params = registry.get_locations(base, id)
            found = False
            try:
                locations = util.query_registry_paginated(session, url, params)
                for loc in locations:
                    resource = util.parse_location(loc, alt_base=args.alt_base)
                    if resource is None:
                        pass
                    elif args.link is not None:
                        try:
                            linkpath = resource.link(args.link)
                            print(f"{id:<20}\t-> {linkpath}")
                            found = True
                            break
                        except AttributeError:
                            log.info("%s doesn't support linking", resource)
                        except OSError as err:
                            log.error("%s: unable to link: %s", id, _link_error(err))
                            break
                    elif args.print0:
                        try:
                            print(str(resource.path), end="\0")
                            found = True
                        except AttributeError:
                            log.info("%s isn't local, skipping", resource)
                    else:
                        print(f"{id:<20}\t{resource}")
                        found = True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    print(f"{id:<20}\t(not found)")
                else:
                    registry.log_error(e)
            n_failed += not found
    return 1 if n_failed else None


def _link_error(err: OSError) -> str:
    # ERROR_PRIVILEGE_NOT_HELD
    if getattr(err, "winerror", None) == 1314:
        return (
            "Windows creates symbolic links only in Developer Mode or with "
            "administrator rights"
        )
    return str(err)


def hash_search_param(value: str) -> dict:
    """Returns the query parameter to search the registry for a full or partial hash.

    A full sha1 is an exact match, which the registry can look up quickly; part
    of a hash needs the slower substring filter.
    """
    import re

    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return {"sha1": value}
    return {"sha1_contains": value}


def search_resources(args):
    # parse commandline args to query dict
    argmap = [
        ("name", "name"),
        ("dtype", "dtype"),
        ("archive", "archive"),
        ("scheme", "scheme"),
    ]
    params = {
        paramname: getattr(args, argname)
        for (paramname, argname) in argmap
        if getattr(args, argname) is not None
    }
    if args.hash is not None:
        params.update(hash_search_param(args.hash))
    if args.experimenter is not None:
        params["experimenter"] = args.experimenter
    if args.animal is not None:
        # the first part of a UUID is enough
        params["animal__istartswith"] = args.animal
    for k, v in args.metadata.items():
        params[f"metadata__{k}"] = v
    for k, v in args.metadata_neq.items():
        params[f"metadata__{k}__neq"] = v
    if len(params) == 0 and args.superseded is not True:
        log.error("nbank search: error: at least one filter parameter is required")
        return 1
    for d in core.search(args.registry_url, superseded=args.superseded, **params):
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
    return 1 if any("error" in result for result in results.values()) else None


def set_resource_metadata(args):
    for key in args.metadata_remove:
        args.metadata[key] = None
    # an empty value clears a field
    fields = {k: v or None for k, v in core_field_args(args).items()}
    n_failed = 0
    for result in core.update(
        args.registry_url,
        *args.id,
        auth=args.auth,
        metadata={**args.metadata, **fields},
        dtype=args.dtype,
    ):
        json.dump(result, fp=sys.stdout, indent=2)
        sys.stdout.write("\n")
        n_failed += "error" in result
    return 1 if n_failed else None


def fetch_resources(args):
    dest = args.dest or Path()
    to_fetch = set(args.ids)
    if args.read_stdin:
        to_fetch.update(name for line in sys.stdin if (name := line.strip()))
    if not to_fetch:
        log.error("nbank fetch: error: no identifiers given")
        return 1
    url, query = registry.get_locations_bulk(args.registry_url, to_fetch)
    with (
        util.registry_session(args.registry_url, auth=args.auth) as session,
        concurrent.futures.ThreadPoolExecutor() as executor,
    ):
        response = tuple(util.query_registry_bulk(session, url, query))
        to_fetch -= {resource["name"] for resource in response}
        if args.verify:
            for resource in response:
                if resource["sha1"] is None:
                    log.warning("%s: no registered hash to check", resource["name"])
        future_to_name = {
            executor.submit(
                util.fetch_resource,
                session,
                resource["locations"],
                # try filename if the server returns it
                (dest / resource.get("filename", resource["name"])),
                extension=args.extension,
                force=args.force,
                alt_base=args.alt_base,
                sha1=resource["sha1"] if args.verify else None,
            ): resource["name"]
            for resource in response
        }
        n_failed = len(to_fetch)
        for future in concurrent.futures.as_completed(future_to_name):
            resource_id = future_to_name[future]
            result = future.result()
            print(f"{resource_id:<20}\t-> {result}")
            n_failed += isinstance(result, Exception)
    for resource_id in to_fetch:
        print(f"{resource_id:<20}\t-> (no locations found)")
    return 1 if n_failed else None


def list_datatypes(args):
    url, params = registry.get_datatypes(args.registry_url)
    installed = readers.installed_readers()
    with util.registry_session(args.registry_url) as session:
        dtypes = list(util.query_registry_paginated(session, url, params))
    for dtype in dtypes:
        line = f"{dtype['name']:<25}\t({dtype['content_type']})"
        if dtype["name"] in installed:
            line += f"\treader: {installed[dtype['name']]}"
        print(line)


def add_datatype(args):
    url, params = registry.add_datatype(
        args.registry_url, args.dtype_name, args.content_type
    )
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        resp = session.post(url, json=params)
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
    with util.registry_session(args.registry_url) as session:
        archives = list(util.query_registry_paginated(session, url, params))
    for arch in archives:
        if arch["scheme"] == "neurobank":
            print(f"{arch['name']:<25}\t{arch['root']}")
        else:
            url = urlunparse((arch["scheme"], arch["root"], "", "", "", ""))
            print(f"{arch['name']:<25}\t{url}")


def check_archive(args):
    """Check an archive against the registry and its policy.

    Files must be readable and match their registry records and hashes, every resource
    registered to the archive must have a file, the key of each resource's location
    must be the name of its file, and ownership and permissions must follow the policy
    (keys and permissions are fixed with --fix). The archive is a path if it's an
    existing directory, and otherwise a name in the registry. Returns 1 if there are
    errors that weren't fixed or the check couldn't be run.
    """
    path = Path(args.archive)
    if not path.is_dir():
        return _check_archive_by_name(args, args.archive)
    archive_cfg = _archive_config(path)
    if archive_cfg is None:
        return 1
    log.info("archive: %s", archive_cfg.path)
    registry_url = archive_cfg.registry
    log.info("registry: %s", registry_url)
    with util.registry_session(registry_url, auth=args.auth) as session:
        name = _registered_name(session, registry_url, archive_cfg.path)
        if name is None:
            return 1
        ok = _check_archive(session, registry_url, name, archive_cfg, args)
    return 0 if ok else 1


def _archive_config(path: Path) -> archive.ArchiveConfig | None:
    """Returns the configuration of the neurobank archive at path, or None (logging an
    error) if there isn't one."""
    try:
        return archive.ArchiveConfig.load(path)
    except FileNotFoundError:
        log.error("error: %s is not a valid neurobank archive", path)
        return None


def _registered_name(session, registry_url: str, archive_path: Path) -> str | None:
    """Returns the registry's name for the archive at archive_path, or None (logging an
    error) if it isn't registered."""
    url, params = registry.find_archive_by_path(registry_url, archive_path)
    info = util.query_registry_first(session, url, params)
    if info is None:
        log.error("No archive associated with '%s' in the registry", archive_path)
        return None
    return info["name"]


def _log_summary(*counts: str, failed: int) -> None:
    """Logs the counts of a transfer, then the number that failed, as one line."""
    text = "Resources " + "; ".join([*counts, f"failed: {failed}"])
    if failed:
        log.error("%s (marked with ✗ above)", text)
    else:
        log.info("%s", text)


def _check_archive_by_name(args, name: str) -> int:
    """Checks the archive with this name in the registry. It must be a neurobank archive
    on this host."""
    registry_url = args.registry_url
    log.info("registry: %s", registry_url)
    with util.registry_session(registry_url, auth=args.auth) as session:
        url, _ = registry.get_archive(registry_url, name)
        info = util.query_registry(session, url)
        if info is None:
            log.error(
                "error: '%s' is not a directory or an archive in the registry", name
            )
            return 1
        if info["scheme"] not in archive.Resource.schemes:
            log.error("error: checking %s archives isn't supported", info["scheme"])
            return 1
        if not Path(info["root"]).is_dir():
            log.error("error: %s is not on this host", info["root"])
            return 1
        ok = _check_registered_archive(session, registry_url, info, args)
    return 0 if ok else 1


def _check_registered_archive(session, registry_url, info, args) -> bool:
    """Checks a neurobank archive on this host, given its registry record: its
    configuration must match the record, then its contents are checked. Returns True if
    there are no errors."""
    name = info["name"]
    root = Path(info["root"])
    log.info("\narchive %s: %s", name, root)
    try:
        archive_cfg = archive.ArchiveConfig.load(root)
    except FileNotFoundError:
        log.error(" - %s is not a valid neurobank archive", root)
        return False
    except (OSError, ValueError, KeyError) as err:
        log.error(" - unable to read nbank.json: %s", err)
        return False
    ok = True
    if archive_cfg.path != root:
        log.error(
            " - registered root resolves to %s; deposits won't find the archive",
            archive_cfg.path,
        )
        ok = False
    if archive_cfg.registry.rstrip("/") != registry_url.rstrip("/"):
        log.error(
            " - nbank.json points to a different registry (%s)",
            archive_cfg.registry,
        )
        ok = False
    try:
        if not _check_archive(session, registry_url, name, archive_cfg, args):
            ok = False
    except OSError as err:
        log.error(" - unable to check: %s", err)
        ok = False
    return ok


def _check_archive(session, registry_url, archive_name, archive_cfg, args) -> bool:
    """Runs and logs the archive checks. Returns True if there are no errors."""
    archive_path = archive_cfg.path
    log.info(
        "retrieving resources that should be in %s from the registry...",
        archive_name,
    )
    expected = check.registry_resources_in_archive(session, registry_url, archive_name)
    log.info(" - resources in the registry: %d", len(expected))
    registered = check.registry_keys_in_archive(
        session, registry_url, archive_name, expected
    )
    keys = {name: key for name, (key, _) in registered.items()}
    log.info("checking ownership and permissions:")
    perm_counts = Counter()
    unable_to_check = False
    try:
        for finding in check.check_archive_permissions(archive_cfg, fix=args.fix):
            perm_counts[finding.status, finding.fixed] += 1
            if not args.verbose:
                continue
            msg = f" - {finding.path} - {finding.status.value} ({finding.detail})"
            if finding.fixed:
                log.info("%s - fixed", msg)
            else:
                log.error("%s", msg)
    except ValueError as err:
        log.error(" - unable to check: %s", err)
        unable_to_check = True
    if not args.verbose:
        for (status, fixed), count in perm_counts.items():
            if fixed:
                log.info(" - %s: fixed %d files or directories", status.value, count)
            else:
                log.error(
                    " - %s: %d files or directories (use -v to list)",
                    status.value,
                    count,
                )
    n_fixed = sum(n for (_, fixed), n in perm_counts.items() if fixed)
    log.info("verifying resources:")
    counts = Counter()
    n_errors = 0
    unregistered = []  # looked up in the registry after the main pass
    incomplete = []  # left over from transfers; may be deleted after the main pass
    wrong_keys = []  # set after the main pass with --fix
    other_names = []  # files whose names differ from the registry's filename
    for finding in check.check_archive_contents(
        archive_path, expected, check_hash=not args.no_hash, keys=keys
    ):
        if finding.status in (check.Status.NO_KEY, check.Status.WRONG_KEY):
            wrong_keys.append(finding)
            continue
        if finding.resource in registered and finding.path is not None:
            filename = registered[finding.resource][1]
            if finding.path.name != filename:
                other_names.append((finding.path, filename))
        if finding.status == check.Status.MISSING_FROM_REGISTRY:
            unregistered.append(finding)
            continue
        if finding.status == check.Status.INCOMPLETE:
            incomplete.append(finding)
            continue
        counts[finding.status] += 1
        n_errors += not finding.ok
        if finding.status == check.Status.MISSING_FROM_ARCHIVE:
            log.error(" - %s: MISSING from the archive", finding.resource)
        elif finding.resource is None:
            log.error(
                " - %s - %s (%s)", finding.path, finding.status.value, finding.detail
            )
        elif not finding.ok:
            log.error(
                " - %s : %s - %s", finding.resource, finding.path, finding.status.value
            )
        elif args.verbose:
            log.info(
                " - %s : %s - %s", finding.resource, finding.path, finding.status.value
            )
    n_keys_set = 0
    key_counts = Counter()
    if wrong_keys:
        log.info("checking location keys:")
    if args.fix:
        refused = dict(_set_keys(session, registry_url, archive_name, wrong_keys))
    for finding in wrong_keys:
        msg = f" - {finding.resource} : {finding.path} - {finding.status.value}"
        if finding.detail:
            msg += f" ({finding.detail})"
        if args.fix:
            error = refused.get(finding.resource)
            if error is None:
                n_keys_set += 1
                if args.verbose:
                    log.info("%s - fixed", msg)
                continue
            log.error(
                " - %s : unable to set the key to '%s': %s",
                finding.resource,
                finding.path.name,
                error,
            )
        elif args.verbose:
            log.error("%s", msg)
        key_counts[finding.status] += 1
        n_errors += 1
    if not args.verbose:
        for status, count in key_counts.items():
            log.error(" - %s: %d resources (use -v to list)", status.value, count)
    log.info(
        "files named other than the registry's filename: %d%s",
        len(other_names),
        "" if args.verbose or not other_names else " (use -v to list)",
    )
    if args.verbose:
        for path, filename in other_names:
            log.info(" - %s (filename: %s)", path, filename)
    prompting = args.fix and _interactive()
    if args.fix and (unregistered or incomplete) and not prompting:
        log.info("not an interactive terminal, so not asking how to resolve problems")
    n_deleted = 0
    for finding in incomplete:
        log.error(" - %s - %s (%s)", finding.path, finding.status.value, finding.detail)
        if prompting and _ask("   [d]elete, [s]kip? ", "ds") == "d":
            try:
                archive.remove(finding.path)
            except OSError as err:
                log.error("   unable to delete %s: %s", finding.path, err)
            else:
                log.info("   - deleted %s", finding.path)
                n_deleted += 1
                continue
        counts[finding.status] += 1
        n_errors += 1
    n_resolved = 0
    if unregistered:
        log.info("looking up files that aren't in this archive's registry records:")
    for finding in check.recheck_unregistered(session, registry_url, unregistered):
        if finding.status == check.Status.MISSING_FROM_REGISTRY:
            log.error(
                " - %s: MISSING from the registry under %s",
                finding.path,
                archive_name,
            )
        else:
            log.error(
                " - %s: %s (%s)", finding.path, finding.status.value, finding.detail
            )
            if prompting and _resolve_elsewhere(
                session, registry_url, archive_name, finding
            ):
                n_resolved += 1
                continue
        counts[finding.status] += 1
        n_errors += 1
    log.info(
        "\nResources in registry: %d; missing from archive: %d; missing from registry: %d; read/verify errors: %d; other layout errors: %d; permission errors: %d; registered elsewhere: %d; location key errors: %d",
        len(expected),
        counts[check.Status.MISSING_FROM_ARCHIVE],
        counts[check.Status.MISSING_FROM_REGISTRY],
        counts[check.Status.UNREADABLE] + counts[check.Status.HASH_MISMATCH],
        counts[check.Status.MISPLACED]
        + counts[check.Status.DUPLICATE]
        + counts[check.Status.UNEXPECTED]
        + counts[check.Status.SYMLINK]
        + counts[check.Status.DIRECTORY]
        + counts[check.Status.INCOMPLETE],
        perm_counts.total() - n_fixed,
        counts[check.Status.REGISTERED_ELSEWHERE]
        + counts[check.Status.UNVERIFIED_ELSEWHERE]
        + counts[check.Status.CHANGED_ELSEWHERE],
        key_counts.total(),
    )
    if args.fix:
        log.info("Permission errors fixed: %d", n_fixed)
        log.info("Location keys set: %d", n_keys_set)
        log.info("Files registered elsewhere resolved: %d", n_resolved)
        log.info("Incomplete transfers and deposits deleted: %d", n_deleted)
    n_errors += perm_counts.total() - n_fixed
    return n_errors == 0 and not unable_to_check


def _set_keys(
    session, registry_url, archive_name, findings
) -> Iterator[tuple[str, str]]:
    """Sets the key of each finding's location to the name of its file. Yields
    (resource, the registry's error) for each key the registry refuses."""
    refused = [
        f
        for f in findings
        if _set_key(session, registry_url, archive_name, f.resource, f.path.name)
    ]
    # a refused key may be held by another location being fixed (two keys swapped,
    # say), so the refused locations' keys, which are wrong anyway, are cleared before
    # they're set again
    for f in refused:
        _set_key(session, registry_url, archive_name, f.resource, None)
    for f in refused:
        error = _set_key(session, registry_url, archive_name, f.resource, f.path.name)
        if error is not None:
            yield f.resource, error


def _set_key(session, registry_url, archive_name, resource, key) -> str | None:
    """Sets the key of a resource's location. Returns the registry's error if it
    refuses, or None."""
    url, body = registry.update_location(registry_url, resource, archive_name, key)
    r = session.patch(url, json=body)
    if r.status_code == httpx.codes.OK:
        return None
    try:
        return "; ".join(registry.error_messages(r))
    except ValueError:
        return r.text


def _interactive() -> bool:
    """True if the user can be asked questions."""
    return sys.stdin.isatty()


def _ask(prompt: str, choices: str) -> str:
    """Asks the user to pick one of choices (single letters). Returns "" on EOF."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return ""
        if len(answer) == 1 and answer in choices:
            return answer


def _resolve_elsewhere(session, registry_url, archive_name, finding) -> bool:
    """Offers to delete a file registered elsewhere, or to add its location.

    Only for files that match the registered hash or whose resource has none. Deleting
    is offered only if the resource has another location, and adding only if the file is
    in the right subdirectory. Returns True if the file was deleted or the location
    added.
    """
    if finding.status not in (
        check.Status.REGISTERED_ELSEWHERE,
        check.Status.UNVERIFIED_ELSEWHERE,
    ):
        return False
    path = finding.path
    options = []
    if finding.locations:
        options.append(("d", "[d]elete this copy"))
    if path.parent.name == archive.id_stub(finding.resource):
        options.append(("a", f"[a]dd {archive_name} as a location"))
    if not options:
        return False
    if finding.status == check.Status.UNVERIFIED_ELSEWHERE:
        log.warning(
            "   the registry has no hash for %s, so this file may not be a copy",
            finding.resource,
        )
    options.append(("s", "[s]kip"))
    choices = "".join(key for key, _ in options)
    answer = _ask(f"   {', '.join(text for _, text in options)}? ", choices)
    if answer == "d":
        try:
            archive.remove(path)
        except OSError as err:
            log.error("   unable to delete %s: %s", path, err)
            return False
        log.info("   - deleted %s", path)
        return True
    if answer == "a":
        url, body = registry.add_location(
            registry_url, finding.resource, archive_name, path.name
        )
        r = session.post(url, json=body)
        if r.status_code != httpx.codes.CREATED:
            log.error("   unable to add location: %s", r.text)
            return False
        log.info("   - added %s as a location for %s", archive_name, finding.resource)
        return True
    return False


def check_registry(args):
    """Check the registry for resources without locations and empty archives.

    Returns 1 if any resource has no locations or the check couldn't be run, 0
    otherwise.
    """
    log.info("registry: %s", args.registry_url)
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        ok = _check_registry(session, args.registry_url)
    return 0 if ok else 1


def check_superseded(args):
    """Report superseded and unregistered resources among args.ids.

    Returns 1 if any is superseded or unregistered.
    """
    ids = list(args.ids)
    if args.read_stdin:
        ids.extend(name for line in sys.stdin if (name := line.strip()))
    ids = list(dict.fromkeys(ids))
    if not ids:
        log.error("nbank check superseded: error: no identifiers given")
        return 1
    records = {}
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        for i in range(0, len(ids), util.bulk_batch_size):
            url, query = registry.get_resource_bulk(
                args.registry_url, ids[i : i + util.bulk_batch_size]
            )
            for record in util.query_registry_bulk(session, url, query):
                records[record["name"]] = record
    n_superseded = n_missing = 0
    for id in ids:
        record = records.get(id)
        if record is None:
            log.error(" - %s: not in the registry", id)
            n_missing += 1
        elif record["superseded_on"] is not None:
            details = [f"superseded on {record['superseded_on']}"]
            if record["superseded_by"]:
                details.append(f"by {record['superseded_by']}")
            if record["superseded_note"]:
                details.append(f"({record['superseded_note']})")
            log.error(" - %s: %s", id, " ".join(details))
            n_superseded += 1
    log.info(
        "%d resources checked; superseded: %d; not in the registry: %d",
        len(ids),
        n_superseded,
        n_missing,
    )
    return 1 if n_superseded or n_missing else 0


def _check_registry(session, registry_url) -> bool:
    """Runs and logs the registry checks. Returns True if there are no errors."""
    counts = Counter()
    for finding in check.check_registry(session, registry_url):
        counts[finding.status] += 1
        if finding.status == check.Status.NO_LOCATION:
            log.error(" - %s: %s", finding.resource, finding.status.value)
        else:
            log.warning(" - archive %s: %s", finding.archive, finding.status.value)
    log.info(
        "\nResources without locations: %d; empty archives: %d",
        counts[check.Status.NO_LOCATION],
        counts[check.Status.EMPTY_ARCHIVE],
    )
    return counts[check.Status.NO_LOCATION] == 0


def check_all(args):
    """Check the registry, then every neurobank archive on this host.

    Other archives are skipped, and a problem with one archive doesn't stop the rest.
    Returns 1 if the registry or any archive has errors.
    """
    registry_url = args.registry_url
    log.info("registry: %s", registry_url)
    failed = []
    skipped = []
    with util.registry_session(registry_url, auth=args.auth) as session:
        if not _check_registry(session, registry_url):
            failed.append("(registry)")
        url, params = registry.get_archives(registry_url)
        for info in util.query_registry_paginated(session, url, params):
            name = info["name"]
            root = Path(info["root"])
            if info["scheme"] not in archive.Resource.schemes:
                skipped.append((name, f"{info['scheme']} archive"))
                continue
            if not root.is_dir():
                skipped.append((name, f"{root} is not on this host"))
                continue
            if not _check_registered_archive(session, registry_url, info, args):
                failed.append(name)
    log.info("\nskipped archives: %d", len(skipped))
    for name, reason in skipped:
        log.info(" - %s: %s", name, reason)
    if failed:
        log.error("failed checks: %s", ", ".join(failed))
    else:
        log.info("all checks passed")
    return 1 if failed else 0


def register_tape(args):
    archive_root = f"{args.tape_name}:{args.file_number}"
    archive_name = args.archive_name or f"{args.tape_name}-{args.file_number}"
    url, params = registry.add_archive(
        args.registry_url,
        name=archive_name,
        scheme="tape",
        root=archive_root,
        accessibility="offline",
    )
    with (
        util.registry_session(args.registry_url, auth=args.auth) as session,
        tarfile.open(args.tar) as tarf,
    ):
        log.info(
            "- creating '%s' archive in the registry with root '%s'",
            archive_name,
            archive_root,
        )
        if not args.dry_run:
            try:
                r = session.post(url, json=params)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                registry.log_error(e)
                return 1
        log.info("- scanning contents of %s", args.tar)
        n_failed = 0
        refused = None
        for tarinfo in tarf:
            path = Path(tarinfo.name)
            if PurePosixPath(tarinfo.name) == PurePosixPath(transfer.manifest_name):
                log.info("  - %s -> manifest, skipping", tarinfo.name)
                continue
            # the contents of a directory with a registered id aren't resources
            if refused and path.is_relative_to(refused):
                log.debug("  - %s -> in %s, skipping", tarinfo.name, refused)
                continue
            # look up the resource
            url, params = registry.get_resource(
                args.registry_url, Path(tarinfo.name).stem
            )
            result = util.query_registry(session, url, params)
            if result is None:
                if tarinfo.isreg():
                    log.info("  - %s -> no match in registry, skipping", tarinfo.name)
                continue
            if tarinfo.isdir():
                log.error(
                    "  ✗ %s -> is a directory, which isn't a resource", tarinfo.name
                )
                n_failed += 1
                refused = path
                continue
            if archive_name in result["locations"]:
                log.info(
                    "  - %s -> already associated with '%s' location",
                    tarinfo.name,
                    archive_name,
                )
            elif args.dry_run:
                log.info(
                    "  - %s -> added location in '%s' (dry run)",
                    tarinfo.name,
                    archive_name,
                )
            else:
                url, params = registry.add_location(
                    args.registry_url, result["name"], archive_name, tarinfo.name
                )
                try:
                    r = session.post(url, json=params)
                    r.raise_for_status()
                    log.info(
                        "  - %s -> added location in '%s'",
                        tarinfo.name,
                        archive_name,
                    )
                except httpx.HTTPStatusError as e:
                    registry.log_error(e)
                    return 1
    return 1 if n_failed else None


def prune_archive(args):
    """Remove files from a neurobank archive that are stored somewhere else.

    Returns 1 if the archive can't be pruned or a resource can't be removed. Resources
    not in the archive, or only in it, are skipped.
    """
    n_failed = 0
    if args.dry_run:
        log.info("DRY RUN")
    log.info("registry: %s", args.registry_url)
    with (
        open(args.resources) as fp,
        util.registry_session(args.registry_url, auth=args.auth) as session,
    ):
        # check that the archive is on the local machine
        url, _ = registry.get_archive(args.registry_url, args.archive_name)
        result = util.query_registry(session, url)
        if result is None:
            log.error("No such archive '%s' in the registry", args.archive_name)
            return 1
        if result["scheme"] not in archive.Resource.schemes:
            log.error("'%s' is not a neurobank archive ", args.archive_name)
            return 1
        if not Path(result["root"]).is_dir():
            log.error(
                "The archive '%s' is not on this host (%s) ",
                args.archive_name,
                result["root"],
            )
            return 1
        log.info("pruning archive: %s (%s)", args.archive_name, result["root"])
        for line in fp:
            resource_id = line.strip()
            if len(resource_id) == 0 or resource_id.startswith("#"):
                continue
            log.info("%s:", resource_id)
            url, query = registry.get_locations(args.registry_url, resource_id)
            response = util.query_registry(session, url, query)
            if response is None:
                log.error("✗ %s: not in registry", resource_id)
                n_failed += 1
                continue
            locations = {loc["archive_name"]: loc for loc in response}
            locations.pop("registry", None)  # remove registry pseudo-location
            if args.archive_name not in locations:
                log.info("  ✗ not in this archive")
            elif len(locations) < 2:
                log.info("  ✗ this archive is the only location for this resource")
            else:
                # this can throw FileNotFound but that shouldn't happen unless
                # something is really wrong
                resource = util.parse_location(locations[args.archive_name])
                if resource is None:
                    log.error("  ✗ resource is not actually present in archive")
                    n_failed += 1
                    continue
                if not args.dry_run and not resource.deletable:
                    log.info("  ✗ insufficient permissions to delete")
                    n_failed += 1
                    continue
                url, query = registry.get_location(
                    args.registry_url, resource_id, args.archive_name
                )
                req = session.build_request("DELETE", url)
                if not args.dry_run:
                    r = session.send(req)
                    if r.status_code != httpx.codes.NO_CONTENT:
                        log.info(
                            "  ✗ unable to remove from registry: %s", r.json()["detail"]
                        )
                        n_failed += 1
                        continue
                log.info("  - removed %s", req.url)
                if not args.dry_run:
                    resource.unlink()
                log.info("  - deleted %s", resource.path)
    return 1 if n_failed else None


def _human_size(nbytes: float) -> str:
    """Returns a byte count in decimal units, e.g. '1.2 GB'."""
    for unit in ("B", "kB", "MB", "GB"):
        if nbytes < 1000:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1000
    return f"{nbytes:.1f} TB"


class Progress:
    """Reports progress reading a resource on one line of stderr, if it's a terminal.

    Call start() for each resource and pass the object as a progress callback. The rate
    shown is over the last `window` seconds. summary() gives the size and average rate
    whether or not stderr is a terminal. As a context manager, clears its line before
    nbank log messages.
    """

    interval = 0.2
    window = 5.0

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = self.stream.isatty()
        self._width = 0
        self.start("")

    def start(self, label: str, total: int | None = None) -> None:
        """Starts reporting on a resource called label, total bytes long if known."""
        self.label = label
        self.total = total
        self._path = None
        self._read: dict[str, int] = {}
        self._started = time.monotonic()

    @property
    def nbytes(self) -> int:
        """Returns the number of bytes of the resource read so far."""
        return sum(self._read.values())

    def summary(self) -> str:
        """Returns the size of the resource read so far and its average rate."""
        nbytes = self.nbytes
        elapsed = time.monotonic() - self._started
        if elapsed <= 0:
            return _human_size(nbytes)
        return f"{_human_size(nbytes)}, {_human_size(nbytes / elapsed)}/s"

    def __call__(self, path: str, nbytes: int) -> None:
        self._read[path] = nbytes
        if not self.enabled:
            return
        now = time.monotonic()
        if path != self._path:
            self._path = path
            self._samples = deque()
        if self._samples and now - self._samples[-1][0] < self.interval:
            return
        self._samples.append((now, nbytes))
        # keep the newest sample that's at least a window old, as the baseline
        while len(self._samples) > 1 and now - self._samples[1][0] >= self.window:
            self._samples.popleft()
        whole = path == self.label
        text = f"  {self.label if whole else f'{self.label}/{path}'}  "
        text += _human_size(nbytes)
        if whole and self.total:
            text += f" / {_human_size(self.total)} ({100 * nbytes / self.total:.0f}%)"
        then, then_bytes = self._samples[0]
        if now > then:
            text += f"  {_human_size((nbytes - then_bytes) / (now - then))}/s"
        self.clear()
        self.stream.write(text)
        self.stream.flush()
        self._width = len(text)

    def clear(self) -> None:
        """Erases the progress line, if there is one."""
        if self._width:
            self.stream.write("\r" + " " * self._width + "\r")
            self.stream.flush()
            self._width = 0

    def _before_log(self, record) -> bool:
        self.clear()
        return True

    def __enter__(self):
        for handler in log.handlers:
            handler.addFilter(self._before_log)
        return self

    def __exit__(self, *exc):
        self.clear()
        for handler in log.handlers:
            handler.removeFilter(self._before_log)


def _tar_lookup(session, registry_url, unregistered: list | None = None):
    """Returns a lookup function for transfer.iter_tar_resources.

    Logs files that aren't registered and appends their names to unregistered, if given.
    """

    def lookup(id, member):
        url, _ = registry.get_resource(registry_url, id)
        record = util.query_registry(session, url)
        if record is None and member.isreg():
            log.info("  ✗ %s -> '%s' not in the registry", member.name, id)
            if unregistered is not None:
                unregistered.append(member.name)
        return record

    return lookup


def check_tar(args):
    """Check the resources in a tar file, tape, or stdin against the registry.

    Unregistered files are reported but aren't errors. With --archive, also reports
    resources registered to that archive that are missing from the tar file (or not
    checked, if reading stopped), and resources in it that aren't registered there.
    Returns 1 if anything fails, is missing, or can't be read.
    """
    log.info("registry: %s", args.registry_url)
    log.info("source: %s", args.tar)
    counts = Counter()
    unregistered = []
    read_error = False
    seen = set()
    not_located = []
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        expected = {}
        if args.archive is not None:
            url, _ = registry.get_archive(args.registry_url, args.archive)
            if util.query_registry(session, url) is None:
                log.error("error: no archive '%s' in the registry", args.archive)
                return 1
            expected = check.registry_resources_in_archive(
                session, args.registry_url, args.archive
            )
            log.info(
                "archive: %s (%d resources in the registry)",
                args.archive,
                len(expected),
            )
        lookup = _tar_lookup(session, args.registry_url, unregistered)
        try:
            with transfer.open_tar(args.tar) as tarf, Progress() as progress:
                for res in transfer.iter_tar_resources(tarf, lookup):
                    seen.add(res.record["name"])
                    if (
                        args.archive is not None
                        and args.archive not in res.record["locations"]
                    ):
                        log.warning(
                            "  - %s -> not registered to %s", res.name, args.archive
                        )
                        not_located.append(res.record["name"])
                    progress.start(res.name, res.size)
                    try:
                        received = transfer.receive_file(
                            None,
                            res.record["name"],
                            res.name,
                            res.data,
                            res.record["sha1"],
                            progress=progress,
                        )
                    except transfer.TarReadError:
                        log.error("  ✗ %s -> unable to read; stopping", res.name)
                        counts["failed"] += 1
                        raise
                    except transfer.TransferError as err:
                        log.error("  ✗ %s -> %s", res.name, err)
                        counts["failed"] += 1
                        continue
                    if received.verified:
                        note = ""
                        counts["ok"] += 1
                    else:
                        note = " (no registered hash to check)"
                        counts["unverified"] += 1
                    log.info("  - %s -> OK%s  (%s)", res.name, note, progress.summary())
        except (OSError, tarfile.TarError) as err:
            log.error("error: unable to read %s: %s", args.tar, err)
            read_error = True
    unread = sorted(set(expected) - seen)
    # after a read error, resources that weren't reached may still be there
    missing, not_checked = ([], unread) if read_error else (unread, [])
    for name in missing:
        log.error(
            "  ✗ %s: registered to %s but MISSING from the tar file", name, args.archive
        )
    for name in not_checked:
        log.warning("  ? %s: not checked, as reading stopped before it", name)
    log.info(
        "\nResources checked: %d; failed: %d; no registered hash: %d; "
        "files not in the registry: %d",
        counts.total(),
        counts["failed"],
        counts["unverified"],
        len(unregistered),
    )
    if args.archive is not None:
        stopped = (
            f"; not checked (reading stopped): {len(not_checked)}" if read_error else ""
        )
        log.info(
            "Missing from the tar file: %d%s; not registered to %s: %d",
            len(missing),
            stopped,
            args.archive,
            len(not_located),
        )
    return 1 if counts["failed"] or missing or read_error else 0


def import_tar(args):
    """Import resources from a tar file, tape, or stdin into a neurobank archive.

    Each registered resource is checked against its hash as it's stored, then added as a
    location. With identifiers (or --from-file), imports only those resources and stops
    reading once they're found. With --dry-run, only reads and checks. Unregistered
    files and resources already in the destination are skipped. Returns 1 if anything
    fails to import, a requested resource isn't found, or the tar file can't be read.
    """
    n_failed = 0
    wanted = None
    if args.ids or args.from_file is not None:
        wanted = _requested_ids(args)
        if wanted is None:
            return 1
        wanted = set(wanted)
    n_requested = None if wanted is None else len(wanted)
    archive_cfg = _archive_config(args.dest)
    if archive_cfg is None:
        return 1
    registry_url = args.registry_url or archive_cfg.registry
    archive_path = archive_cfg.path  # this will resolve the path
    log.info("registry: %s", registry_url)
    n_imported = n_already = total = 0
    with util.registry_session(registry_url, auth=args.auth) as session:
        archive_name = _registered_name(session, registry_url, archive_path)
        if archive_name is None:
            return 1
        log.info("destination archive: %s (%s)", archive_name, archive_path)
        log.info("source: %s", args.tar)
        if args.dry_run:
            log.info("DRY RUN: checking resources without importing them")

        tar_lookup = _tar_lookup(session, registry_url)

        def lookup(id, member):
            # only resources that were asked for are looked up
            if wanted is not None and id not in wanted:
                return None
            return tar_lookup(id, member)

        dest = None if args.dry_run else archive_cfg

        def import_one(res, progress) -> bool:
            """Imports a resource from the tar file. Returns False if it fails."""
            nonlocal n_imported, n_already, total
            id = res.record["name"]
            if archive_name in res.record["locations"]:
                log.info(
                    "  ✗ %s -> '%s' is already in the destination archive", res.name, id
                )
                n_already += 1
                return True
            progress.start(res.name, res.size)
            try:
                received = transfer.receive_file(
                    dest, id, res.name, res.data, res.record["sha1"], progress=progress
                )
                if dest is not None:
                    transfer.add_location(
                        session, registry_url, id, archive_name, received.path
                    )
            except transfer.TarReadError:
                log.error("  ✗ %s -> unable to read; stopping", res.name)
                raise
            except transfer.TransferError as err:
                log.error("  ✗ %s -> %s", res.name, err)
                return False
            n_imported += 1
            total += progress.nbytes
            result = received.path if dest is not None else "OK"
            note = "" if received.verified else " (no registered hash to check)"
            log.info("  - %s -> %s%s  (%s)", res.name, result, note, progress.summary())
            return True

        read_error = False
        try:
            with transfer.open_tar(args.tar) as tarf, Progress() as progress:
                for res in transfer.iter_tar_resources(tarf, lookup):
                    n_failed += not import_one(res, progress)
                    if wanted is not None:
                        wanted.discard(res.record["name"])
                        if not wanted:
                            # everything asked for is found; don't read the rest
                            break
        except (OSError, tarfile.TarError) as err:
            log.error("error: unable to read %s: %s", args.tar, err)
            n_failed += 1
            read_error = True
    for id in sorted(wanted or ()):
        reason = (
            "not reached, as reading stopped" if read_error else "not in the tar file"
        )
        log.error("  ✗ %s -> %s", id, reason)
        n_failed += 1
    counts = [] if n_requested is None else [f"requested: {n_requested}"]
    _log_summary(
        *counts,
        f"{'checked' if args.dry_run else 'imported'}: {n_imported} "
        f"({_human_size(total)})",
        f"already in destination: {n_already}",
        failed=n_failed,
    )
    return 1 if n_failed else None


def _read_ids(path: Path) -> list[str]:
    """Returns the identifiers in a file, one per line, or in stdin if path is '-'."""
    if str(path) == "-":
        text = sys.stdin.read()
    else:
        text = path.read_text()
    return [line.strip() for line in text.splitlines() if line.strip()]


def _manifest_records(args, sources) -> dict | None:
    """Returns the registry records of sources for a manifest, by id.

    Returns None, after logging why, if a manifest can't be written.
    """
    for source in sources:
        if source.path.name == transfer.manifest_name:
            log.error(
                "error: '%s' is stored as %s, so there can't be a manifest",
                source.id,
                transfer.manifest_name,
            )
            return None
    existing = args.out / transfer.manifest_name
    if args.out.is_dir() and (existing.exists() or existing.is_symlink()):
        log.error("error: '%s' already exists", existing)
        return None
    ids = [source.id for source in sources]
    records = {}
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        for i in range(0, len(ids), util.bulk_batch_size):
            url, query = registry.get_resource_bulk(
                args.registry_url, ids[i : i + util.bulk_batch_size]
            )
            for record in util.query_registry_bulk(session, url, query):
                records[record["name"]] = record
    return records


def _manifest_entry(source, record: dict | None) -> dict:
    """Returns the manifest entry for an exported resource.

    Locations are left out, as they describe where the resource is stored here.
    """
    entry = {"name": source.id, "path": source.path.name, "sha1": source.sha1}
    if record is not None:
        entry.update((k, v) for k, v in record.items() if k != "locations")
    return entry


def export_resources(args):
    """Write resources from this host's archives to a tar file, zip file, or directory.

    Resources that can't be read or don't match their hashes are left out. With
    --manifest, the registry records of those written are added last, as manifest.json.
    Returns 1 if anything is left out or the export can't be run.
    """
    if args.compress and args.out.suffix.lower() != ".zip":
        log.error("error: --compress only applies to zip files")
        return 1
    ids = _requested_ids(args)
    if ids is None:
        return 1
    log.info("registry: %s", args.registry_url)
    n_failed = n_written = total = 0
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        sources = list(
            transfer.find_sources(
                session,
                args.registry_url,
                ids,
                archive=args.archive,
                alt_base=args.alt_base,
            )
        )
    n_requested = len(sources)

    def summarize():
        _log_summary(
            f"requested: {n_requested}",
            f"exported: {n_written} ({_human_size(total)})",
            failed=n_failed,
        )

    for source in sources:
        if not source.ok:
            log.error("  ✗ %s -> %s", source.id, source.error)
            n_failed += 1
    sources = [source for source in sources if source.ok]
    if not sources:
        summarize()
        return 1
    if args.manifest:
        records = _manifest_records(args, sources)
        if records is None:
            return 1
        exported = []
    log.info("writing %d resource(s) to %s", len(sources), args.out)
    try:
        with (
            transfer.open_export(args.out, compress=args.compress) as writer,
            Progress() as progress,
        ):
            for source in sources:
                try:
                    size = source.path.stat().st_size
                except OSError:
                    size = None
                progress.start(source.path.name, size)
                try:
                    verified = transfer.write_resource(writer, source, progress)
                except transfer.TransferError as err:
                    log.error("  ✗ %s -> %s", source.id, err)
                    n_failed += 1
                    continue
                n_written += 1
                total += progress.nbytes
                if args.manifest:
                    exported.append(_manifest_entry(source, records.get(source.id)))
                note = "" if verified else " (no registered hash to check)"
                log.info(
                    "  - %s -> %s%s  (%s)",
                    source.id,
                    source.path.name,
                    note,
                    progress.summary(),
                )
            if args.manifest:
                transfer.write_manifest(
                    writer,
                    {
                        "registry": args.registry_url,
                        "exported": datetime.datetime.now(datetime.UTC).isoformat(
                            timespec="seconds"
                        ),
                        "nbank_version": __version__,
                        "resources": exported,
                    },
                )
                log.info("  - %s", transfer.manifest_name)
    except (OSError, ValueError, transfer.TransferError) as err:
        log.error("error: unable to export to %s: %s", args.out, err)
        return 1
    summarize()
    return 1 if n_failed else None


def _requested_ids(args) -> list[str] | None:
    """Returns the ids from the command line and --from-file.

    Returns None, after logging why, if there aren't any.
    """
    ids = list(args.ids)
    if args.from_file is not None:
        try:
            ids += _read_ids(args.from_file)
        except OSError as err:
            log.error("error: unable to read %s: %s", args.from_file, err)
            return None
    if not ids:
        log.error("error: no identifiers given")
        return None
    return list(dict.fromkeys(ids))


def copy_resources(args):
    """Copy resources from the archives on this host into another archive.

    Each is checked against its hash as it's stored, then added as a location. Resources
    already in the destination are skipped. With --dry-run, only reads and checks.
    Returns 1 if anything fails.
    """
    ids = _requested_ids(args)
    if ids is None:
        return 1
    archive_cfg = _archive_config(args.dest)
    if archive_cfg is None:
        return 1
    registry_url = args.registry_url or archive_cfg.registry
    archive_path = archive_cfg.path
    log.info("registry: %s", registry_url)
    n_failed = n_copied = total = 0
    with util.registry_session(registry_url, auth=args.auth) as session:
        dest_name = _registered_name(session, registry_url, archive_path)
        if dest_name is None:
            return 1
        if args.archive == dest_name:
            log.error("error: the source and destination archives are the same")
            return 1
        log.info("destination archive: %s (%s)", dest_name, archive_path)
        if args.dry_run:
            log.info("DRY RUN: checking resources without copying them")
        already = transfer.located_in(session, registry_url, ids, dest_name)
        for id in ids:
            if id in already:
                log.info("  - %s -> already in the destination archive", id)
        sources = list(
            transfer.find_sources(
                session,
                registry_url,
                [id for id in ids if id not in already],
                archive=args.archive,
                alt_base=args.alt_base,
            )
        )
        for source in sources:
            if not source.ok:
                log.error("  ✗ %s -> %s", source.id, source.error)
                n_failed += 1
        dest = None if args.dry_run else archive_cfg
        with Progress() as progress:
            for source in sources:
                if not source.ok:
                    continue
                try:
                    size = source.path.stat().st_size
                except OSError:
                    size = None
                progress.start(source.path.name, size)
                try:
                    received = transfer.receive_source(dest, source, progress)
                    if dest is not None:
                        transfer.add_location(
                            session, registry_url, source.id, dest_name, received.path
                        )
                except transfer.TransferError as err:
                    log.error("  ✗ %s -> %s", source.id, err)
                    n_failed += 1
                    continue
                n_copied += 1
                total += progress.nbytes
                result = received.path if dest is not None else "OK"
                note = "" if received.verified else " (no registered hash to check)"
                log.info(
                    "  - %s -> %s%s  (%s)",
                    source.id,
                    result,
                    note,
                    progress.summary(),
                )
    _log_summary(
        f"requested: {len(ids)}",
        f"{'checked' if args.dry_run else 'copied'}: {n_copied} ({_human_size(total)})",
        f"already in destination: {len(already)}",
        failed=n_failed,
    )
    return 1 if n_failed else None


def verify_file_hash(args):
    """Returns 1 if any file is missing or doesn't match a registry record, or
    with --metadata, if its metadata doesn't match or can't be read."""
    from nbank.util import id_from_fname

    n_failed = 0
    found_readers = {}
    for path in args.files:
        if not path.exists():
            print(f"{path}: no such file or directory")
            n_failed += 1
            continue
        if path.is_dir():
            print(f"{path}: is a directory, which isn't a resource")
            n_failed += 1
            continue
        # if the name isn't a registered id, look for the file's hash instead
        matched = []
        try:
            test_id = id_from_fname(path)
            if core.verify(args.registry_url, path, id=test_id):
                print(f"{path}: OK")
                if args.metadata:
                    matched.append(core.describe(args.registry_url, test_id))
            else:
                print(f"{path}: FAILED to match record for {test_id}")
                n_failed += 1
        except ValueError:
            for resource in core.verify(args.registry_url, path):
                print(f"{path}: matches registry resource {resource['name']}")
                matched.append(resource)
            if not matched:
                print(f"{path}: no matches in registry")
                n_failed += 1
        if args.metadata:
            for record in matched:
                if not check_file_metadata(path, record, found_readers):
                    n_failed += 1
    return 1 if n_failed else None


def check_file_metadata(path: Path, record: dict, found_readers: dict) -> bool:
    """Prints whether the metadata that the reader for record's dtype reads from
    path matches the record. Returns False if it doesn't, or can't be read.
    found_readers caches the reader for each dtype."""
    dtype = record.get("dtype")
    if dtype is None:
        print(f"{path}: no dtype registered; metadata not checked")
        return True
    if dtype not in found_readers:
        try:
            found_readers[dtype] = readers.find_reader(dtype)
        except RuntimeError as err:
            found_readers[dtype] = err
    reader = found_readers[dtype]
    if isinstance(reader, RuntimeError):
        print(f"{path}: metadata FAILED: {reader}")
        return False
    if reader is None:
        print(f"{path}: no metadata reader for {dtype}; metadata not checked")
        return True
    try:
        from_file = readers.read_file_metadata(reader, dtype, path)
    except ValueError as err:
        print(f"{path}: metadata FAILED: {err}")
        return False
    log.debug("   from the file: %s", from_file)
    conflicts = readers.compare_metadata(from_file, readers.registered_metadata(record))
    if conflicts:
        print(
            f"{path}: metadata FAILED to match record for {record['name']} "
            f"({'; '.join(conflicts)})"
        )
        return False
    print(f"{path}: metadata OK")
    return True


# commands that write or check archives, which can't be done on Windows
_archive_commands = frozenset(
    {
        init_archive,
        store_resources,
        copy_resources,
        import_tar,
        prune_archive,
        register_tape,
        check_archive,
        check_all,
    }
)


# Variables:
# End:
