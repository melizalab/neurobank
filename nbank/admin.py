# -*- mode: python -*-
"""This script is used for administrative tasks on archives: and the registry. It
is intended only for unusual situations that can't easily be fixed manually. For
example, if a lot of files were deposited erroneously.

"""

import logging
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


def update_hashes(args):
    if args.dry_run:
        log.info("DRY RUN")
    to_update = []
    with open(args.resources) as fp:
        for line in fp:
            resource_id = line.strip()
            if len(resource_id) > 0 and not resource_id.startswith("#"):
                to_update.append(resource_id)
    n_to_update = len(to_update)
    url, query = registry.get_locations_bulk(args.registry_url, to_update)
    with httpx.Client(auth=args.auth) as session:
        for i, resource in enumerate(util.query_registry_bulk(session, url, query)):
            log.info(
                "- [%d/%d] %s (current hash: %s)",
                i,
                n_to_update,
                resource["name"],
                resource["sha1"],
            )
            for loc in resource["locations"]:
                try:
                    location = util.parse_location(loc)
                    hash = util.hash(location.path)
                    if hash == resource["sha1"]:
                        log.info("    - already has the right hash")
                    elif args.dry_run:
                        log.info("    - would update hash to %s", hash)
                    else:
                        url, params = registry.get_resource(
                            args.registry_url, resource["name"]
                        )
                        r = session.patch(url, json={"sha1": hash})
                        r.raise_for_status()
                        log.info("    - updated hash to %s", hash)
                    break
                except FileNotFoundError:
                    log.debug("    - %s is not local, skipping", loc)
                except httpx.HTTPStatusError as err:
                    registry.log_error(err)


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

    pp = sub.add_parser("update-hash", help="update hashes in the registry")
    pp.set_defaults(func=update_hashes)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="if set, don't actually update anything",
        action="store_true",
    )
    pp.add_argument(
        "resources", type=Path, help="file with a list of resources to update"
    )

    args = p.parse_args()
    if not hasattr(args, "func"):
        p.print_usage()
        p.exit(0)
    setup_log(log, args.debug)
    log.info("nbank admin version: %s", __version__)
    args.func(args)
