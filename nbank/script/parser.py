# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""The nbank command's parser, and the types and helpers for its options."""

import argparse
import json
import logging
from pathlib import Path

from nbank import (
    __version__,
    archive,
    registry,
    transfer,
)
from nbank.script import archives, checks, resources, transfers

log = logging.getLogger("nbank")


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
    """An argparse action that collects KEY=VALUE arguments into a dict, as
    nbank's -k does.

    Each value is read as JSON if it parses (numbers, true, false, null, lists and
    objects; True, False and None are accepted as Python spells them), and
    otherwise kept as a string. A key that is a field of the resource
    (experimenter, animal) is refused, as those have options of their own.
    """

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

    def __call__(self, parser, namespace, values, option_string=None):
        # a copy, so that the parser's default isn't changed
        kv = dict(getattr(namespace, self.dest) or {})
        if values.count("=") != 1:
            parser.error(f"{option_string} {values}: needs key=value")
        key, val = values.split("=")
        if key in registry.core_fields:
            parser.error(f"{option_string} {key}: {key} is not metadata; use --{key}")
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
env_alt_base = "NBANK_ALT_BASE"


def add_alt_base_arg(pp):
    """Adds --alt-base to a subcommand parser that reads from archives."""
    pp.add_argument(
        "--alt-base",
        type=Path,
        metavar="DIR",
        help="the directory that holds the archives on this host, if they're not at "
        "their registered roots (e.g. a share mounted from another host): "
        "/home/data/starlings is read as DIR/starlings. Default is to use the "
        f"environment variable '{env_alt_base}'",
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
        "--rename",
        action="store_true",
        help="with --fix, rename files to the registry's filename (the id and the "
        "dtype's extension) and set their keys",
    )
    pp.add_argument(
        "--no-hash",
        action="store_true",
        help="don't verify file hashes (much faster for large archives)",
    )


_keyval_help = " VALUE is read as JSON if it parses, otherwise as a string)"


def add_keyval_arg(pp, flag: str, dest: str, help: str):
    """Adds an option taking KEY=VALUE metadata fields, which can be repeated."""
    pp.add_argument(
        flag,
        help=help + ";" + _keyval_help,
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest=dest,
    )


def add_dry_run_arg(pp, help: str):
    """Adds -y/--dry-run to a subcommand parser."""
    pp.add_argument("-y", "--dry-run", help=help, action="store_true")


def add_stdin_arg(pp, what: str = "identifiers from stdin, one per line"):
    """Adds -@, to read more of a subcommand's arguments from stdin."""
    pp.add_argument(
        "-@", dest="read_stdin", action="store_true", help=f"read additional {what}"
    )


def add_from_file_arg(pp, what: str = "identifiers"):
    """Adds -f/--from-file, to read the ids of the resources from a file."""
    pp.add_argument(
        "-f",
        "--from-file",
        type=Path,
        help=f"read {what} from FILE, one per line ('-' for standard input)",
        metavar="FILE",
    )


def add_source_archive_arg(pp, help: str = "only read copies in this archive"):
    """Adds -a/--archive, the name of an archive, to a subcommand parser."""
    pp.add_argument("-a", "--archive", help=help)


def add_restore_arg(pp):
    """Adds --restore to a subcommand parser that copies into an archive."""
    pp.add_argument(
        "--restore",
        help="restore resources the registry lists in the destination archive whose "
        "files are missing from it",
        action="store_true",
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
        f"Default is to use the environment variable '{registry._env_registry}'. "
        "Commands that store resources in an archive use the registry in its "
        "nbank.json",
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

    # each subcommand sets func, and as needed registry_in_archive (it can use the
    # registry in an archive's nbank.json) and posix_archive (it writes or checks
    # archives, which can't be done on Windows)
    sub = p.add_subparsers(title="subcommands")

    pp = sub.add_parser("registry", help="registry information")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser(
        "info", help="show the registry's version, and who it sees you as"
    )
    pp.set_defaults(func=resources.registry_info)

    pp = sub.add_parser("deposit", help="deposit resource(s)")
    pp.set_defaults(
        func=resources.store_resources, registry_in_archive=True, posix_archive=True
    )
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
    add_dry_run_arg(
        pp,
        "run all pre-flight checks, including asking the registry whether it "
        "would accept each resource, but don't register or move any files",
    )
    add_keyval_arg(
        pp,
        "-k",
        "metadata",
        "specify metadata field (use multiple -k for multiple values",
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
    add_stdin_arg(pp, "file names from stdin")
    pp.add_argument(
        "file", nargs="+", type=Path, help="path of file(s) to add to the repository"
    )

    pp = sub.add_parser("locate", help="locate local resource(s)")
    pp.set_defaults(func=resources.locate_resources, registry_in_archive=True)
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
    pp.set_defaults(func=resources.search_resources)
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
    add_keyval_arg(
        pp,
        "-k",
        "metadata",
        "filter by metadata field (use multiple -k for multiple values",
    )
    add_keyval_arg(
        pp,
        "-K",
        "metadata_neq",
        "exclude by metadata field (use multiple -K for multiple values",
    )
    add_core_field_args(pp, "search")
    group = pp.add_mutually_exclusive_group()
    group.add_argument(
        "--only-superseded",
        action="store_const",
        const=True,
        default=False,
        dest="superseded",
        help="find only superseded resources (by default they're left out)",
    )
    group.add_argument(
        "--with-superseded",
        action="store_const",
        const=None,
        dest="superseded",
        help="find superseded resources as well as current ones",
    )
    pp.add_argument("name", help="resource name or fragment to search by", nargs="?")

    pp = sub.add_parser("info", help="get info from registry about resource(s)")
    pp.set_defaults(func=resources.get_resource_info)
    pp.add_argument("id", nargs="+", help="the identifier of the resource")

    pp = sub.add_parser(
        "verify",
        help="compute sha1 hash and check that it matches a record in the database",
    )
    pp.set_defaults(func=resources.verify_file_hash)
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
    pp.set_defaults(func=resources.set_resource_metadata)
    add_keyval_arg(
        pp,
        "-k",
        "metadata",
        "set metadata key=value, replacing any previous value for this key (use "
        "multiple -k for multiple fields",
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
    pp.set_defaults(func=resources.fetch_resources)
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
    add_stdin_arg(pp)
    pp.add_argument(
        "--tar",
        type=Path,
        metavar="TAR",
        help="read the resources from a tar file, a tape device, or '-' for standard "
        "input, instead of their locations. A member is a resource if its name, "
        "without directories or extension, is the id. Each is checked against its "
        "registered hash",
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
    pp.set_defaults(func=transfers.export_resources)
    add_source_archive_arg(pp)
    add_from_file_arg(pp)
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
    pp.set_defaults(
        func=transfers.copy_resources, registry_in_archive=True, posix_archive=True
    )
    add_dry_run_arg(
        pp,
        "check the resources against the registry without copying any files "
        "or changing the registry",
    )
    add_restore_arg(pp)
    add_source_archive_arg(pp)
    add_from_file_arg(pp)
    add_alt_base_arg(pp)
    pp.add_argument("dest", type=Path, help="path of the destination neurobank archive")
    pp.add_argument("ids", nargs="*", help="identifier(s) of the resource(s) to copy")

    pp = sub.add_parser("dtype", help="list and add data types")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser("list", help="list datatypes")
    pp.set_defaults(func=resources.list_datatypes)

    pp = ppsub.add_parser("add", help="add datatype")
    pp.add_argument("dtype_name", help="a unique name for the data type")
    pp.add_argument("content_type", help="the MIME content-type for the data type")
    pp.set_defaults(func=resources.add_datatype)

    pp = sub.add_parser("archive", help="list and manipulate archives")
    ppsub = pp.add_subparsers(title="subcommands")

    pp = ppsub.add_parser("init", help="create a neurobank archive and register it")
    pp.set_defaults(func=archives.init_archive, posix_archive=True)
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
    pp.set_defaults(func=archives.list_archives)
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
    pp.set_defaults(func=archives.register_tape, posix_archive=True)
    add_dry_run_arg(pp, "don't make any changes to the registry")
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
    pp.set_defaults(func=archives.prune_archive, posix_archive=True)
    add_dry_run_arg(pp, "don't delete any files or make any changes to the registry")
    pp.add_argument("archive_name", type=str, help="name of the archive to prune")
    pp.add_argument("resources", type=Path, help="file with list of resources to prune")

    pp = ppsub.add_parser(
        "import-tar",
        help="import resources from a tar file to a neurobank archive",
    )
    pp.set_defaults(
        func=transfers.import_tar, registry_in_archive=True, posix_archive=True
    )
    add_dry_run_arg(
        pp,
        "check the resources against the registry without copying any files "
        "or changing the registry",
    )
    add_restore_arg(pp)
    pp.add_argument(
        "tar",
        type=Path,
        help="tar file with the resources to import: a file, a tape device, or "
        "'-' for standard input",
    )
    add_from_file_arg(pp, "identifiers of the resources to import")
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
    pp.set_defaults(func=checks.check_registry)

    pp = ppsub.add_parser(
        "archive",
        help="check an archive's contents against the registry, and its "
        "ownership and permissions against its policy",
    )
    pp.set_defaults(
        func=checks.check_archive, registry_in_archive=True, posix_archive=True
    )
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
    pp.set_defaults(func=checks.check_all, posix_archive=True)
    add_check_options(pp)

    pp = ppsub.add_parser(
        "superseded",
        help="check that resources aren't superseded, e.g. the inputs to an analysis",
    )
    pp.set_defaults(func=checks.check_superseded)
    add_stdin_arg(pp)
    pp.add_argument("ids", nargs="*", help="identifier(s) of the resources")

    pp = ppsub.add_parser(
        "tar",
        help="check the resources in a tar file (or tape) against the registry",
    )
    pp.set_defaults(func=checks.check_tar)
    add_source_archive_arg(
        pp,
        "also check that every resource the registry places in this archive "
        "is in the tar file",
    )
    pp.add_argument(
        "tar",
        type=Path,
        help="the tar file: a file, a tape device, or '-' for standard input",
    )

    return p
