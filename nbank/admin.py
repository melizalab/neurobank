# -*- mode: python -*-
"""This script is used for administrative tasks on archives: and the registry. It
is intended only for unusual situations that can't easily be fixed manually. For
example, if a lot of files were deposited erroneously.

"""

import logging
import tarfile
from pathlib import Path

import httpx

from nbank import __version__, archive, registry, util
from nbank.script import setup_log, userpwd

log = logging.getLogger("nbank")  # root logger


def delete_resource(
    resource_id: str, *, session: httpx.Client, registry_url: str, dry_run: bool = False
):
    log.info("%s:", resource_id)
    url, params = registry.get_locations(registry_url, resource_id)
    for loc in util.query_registry_paginated(session, url, params):
        partial = util.parse_location(loc)
        if not isinstance(partial, Path):
            continue
        try:
            path = archive.resolve_extension(partial)
        except FileNotFoundError:
            log.info("  - %s has already been deleted", partial)
            continue
        log.info("  ✗ deleted %s", path)
        if not dry_run:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
    url, params = registry.get_resource(args.registry_url, resource_id)
    req = session.build_request("DELETE", url)
    log.info("  ✗ purged %s", req.url)
    if not dry_run:
        r = session.send(req)
        r.raise_for_status()


def delete_resources(args):
    if args.dry_run:
        log.info("DRY RUN")

    with open(args.resources) as fp, httpx.Client(auth=args.auth) as session:
        for line in fp:
            resource_id = line.strip()
            if len(resource_id) == 0 or resource_id.startswith("#"):
                continue
            try:
                delete_resource(
                    resource_id,
                    session=session,
                    registry_url=args.registry_url,
                    dry_run=args.dry_run,
                )
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 404:
                    log.error("  - not in the registry, skipping")
                else:
                    raise err

def tar_resources(args):
    log.info("- transferring resources to %s", args.dest)
    resource_ids = set()
    with open(args.resources) as fp:
        for line in fp:
            resource_id = line.strip()
            if len(resource_id) == 0 or resource_id.startswith("#"):
                continue
            resource_ids.add(resource_id)

    log.info("- looking up %d resource id(s) in %s archive", len(resource_ids), args.archive)
    with httpx.Client(auth=args.auth) as session, tarfile.open(args.dest, mode="a") as tarf:
        url, query = registry.get_locations_bulk(args.registry_url, resource_ids, archive=args.archive)
        for resource in util.query_registry_bulk(session, url, query):
            for location in resource["locations"]:
                if (loc := util.parse_location(location)) is not None:
                    log.info("   - %s -> %s", resource_id, loc.path)
                    tarf.add(loc.path, arcname=loc.path.name)
                    resource_ids.remove(resource_id)
                    break
    if len(resource_ids) > 0:
        log.warning("- warning: the following resources could not be located: %s", ",".join(resource_ids))


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
    sub = p.add_subparsers(title="subcommands")

    pp = sub.add_parser(
        "delete", help="delete resources from local archives and registry"
    )
    pp.set_defaults(func=delete_resources)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="if set, don't actually delete anything",
        action="store_true",
    )
    pp.add_argument(
        "resources", type=Path, help="file with a list of resources to delete"
    )

    pp = sub.add_parser(
        "tar", help="transfer resources from local archives to a tar file for writing to tape"
    )
    pp.set_defaults(func=tar_resources)
    pp.add_argument(
        "resources", type=Path, help="file with a list of resources to transfer"
    )
    pp.add_argument(
        "archive", type=str, help="name of the source archive (must be local)"
    )
    pp.add_argument(
        "dest", type=Path, help="name of the destination tar file"
    )
    # pp.add_argument(
    #     "archive_index", type=int, help="index of the file on the tape where the archive will be stored"
    # )


    args = p.parse_args()
    if not hasattr(args, "func"):
        p.print_usage()
        p.exit(0)
    setup_log(log, args.debug)
    log.info("nbank admin version: %s", __version__)
    args.func(args)
