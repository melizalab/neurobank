# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""This script is used for administrative tasks on archives: and the registry. It
is intended only for unusual situations that can't easily be fixed manually. For
example, if a lot of files were deposited erroneously. 

"""
import logging
from pathlib import Path
import requests as rq
from nbank import __version__, registry, archive, util
from nbank.script import setup_log, userpwd

log = logging.getLogger("nbank")  # root logger


def delete_resources(args):
    if args.dry_run:
        log.info("DRY RUN")

    with open(args.resources, "rt") as fp, rq.Session() as session:
        for line in fp:
            resource_id = line.strip()
            if len(resource_id) == 0 or resource_id.startswith("#"):
                continue
            log.info("%s:", resource_id)
            log.info("- deleting from local archives:")
            url, params = registry.get_locations(args.registry_url, resource_id)
            for loc in util.query_registry_paginated(session, url, params):
                partial = util.parse_location(loc)
                if isinstance(partial, Path):
                    path = archive.resolve_extension(partial)
                    log.info("  ✗ %s", path)
                    if not args.dry_run:
                        if path.is_dir():
                            path.rmdir()
                        else:
                            path.unlink()
            log.info("- purging from registry:")
            url, params = registry.get_resource(args.registry_url, resource_id)
            req = rq.Request("DELETE", url)
            prepped = session.prepare_request(req)
            log.info("  ✗ %s", prepped.url)
            if not args.dry_run:
                session.send(prepped)


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
    sub = p.add_subparsers(title="subcommands")

    pp = sub.add_parser(
        "delete", help="delete resources from local archives: and registry"
    )
    pp.set_defaults(func=delete_resources)
    pp.add_argument(
        "--dry-run",
        "-y",
        help="if set, don't actually delete anything",
        action="store_true",
    )
    pp.add_argument(
        "resources", type=Path, help="file with a list of resources to delete"
    )

    args = p.parse_args()
    if not hasattr(args, "func"):
        p.print_usage()
        p.exit(0)
    setup_log(log, args.debug)
    log.info("nbank admin version: %s", __version__)
    args.func(args)
