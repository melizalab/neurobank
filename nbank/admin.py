# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""This script is used to delete resources from archives and the registry. It is
intended only for unusual situations where many files were deposited
erroneously. Save the names of the entries in a file and then run this script."""
import logging
from pathlib import Path
from nbank import __version__, core, registry, archive
from nbank.script import setup_log, userpwd

log = logging.getLogger("nbank")  # root logger


def delete_resource_files(args):
    """Delete resource files from the archive. This should happen before deleting the registry entry."""
    with open(args.resources, "rt") as fp:
        for line in fp:
            resource_id = line.strip()
            if len(resource_id) == 0 or resource_id.startswith("#"):
                continue
            for partial in core.find(args.registry_url, resource_id):
                if isinstance(partial, Path):
                    path = archive.resolve_extension(partial)
                    log.info("%s: deleting %s", resource_id, path)
                    if not args.dry_run:
                        path.unlink()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="perform administrative tasks on the registry/archive"
    )
    p.add_argument("--debug", help="show verbose log messages", action="store_true")
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
    p.add_argument(
        "--dry-run",
        "-y",
        help="if set, don't actually delete anything",
        action="store_true",
    )
    sub = p.add_subparsers(title="subcommands")

    pp = sub.add_parser(
        "delete-files", help="delete files for resources from local archives"
    )
    pp.set_defaults(func=delete_resource_files)
    pp.add_argument(
        "resources", type=Path, help="file with a list of resources to delete"
    )

    pp = sub.add_parser(
        "delete-records", help="delete records for resources from the registry"
    )
    pp.set_defaults(func=delete_resource_files)
    pp.add_argument(
        "resources", type=Path, help="file with a list of resources to delete"
    )

    args = p.parse_args()
    if not hasattr(args, "func"):
        p.print_usage()
        p.exit()

    setup_log(log, args.debug)
    log.info("nbank admin version: %s", __version__)
    if args.dry_run:
        log.info("DRY RUN")

    args.func(args)
