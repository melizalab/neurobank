# -*- mode: python -*-
"""Administrative tasks on archives and the registry.

For unusual situations that can't easily be fixed by hand, such as a lot of
files deposited by mistake.

"""

import datetime
import json
import logging
from contextlib import nullcontext
from netrc import NetrcParseError
from pathlib import Path
from typing import TextIO

import httpx

from nbank import __version__, core, registry, util
from nbank.script import setup_log, userpwd

log = logging.getLogger("nbank")  # root logger


def delete_resource(
    resource_id: str,
    *,
    session: httpx.Client,
    registry_url: str,
    dry_run: bool = False,
    record_fp: TextIO | None = None,
):
    """Delete a resource from its local archives and purge it from the registry.

    If record_fp is given, the purged registry record is written to it as one
    line of JSON.

    """
    log.info("%s:", resource_id)
    url, params = registry.get_locations(registry_url, resource_id)
    for loc in util.query_registry_paginated(session, url, params):
        log.debug("   - attempting to delete %s from %s", resource_id, loc)
        location = util.parse_location(loc)
        if location is None:
            log.info(
                "  - %s has already been deleted from %s",
                resource_id,
                loc["archive_name"],
            )
        elif not dry_run:
            try:
                location.unlink()
                log.info("  ✗ deleted %s", location.path)
            except AttributeError:
                log.debug("   - %s is not deletable, skipping", location)
    url, params = registry.get_resource(registry_url, resource_id)
    record = None
    if record_fp is not None and not dry_run:
        r = session.get(url)
        r.raise_for_status()
        record = r.json()
    req = session.build_request("DELETE", url)
    log.info("  ✗ purged %s", req.url)
    if not dry_run:
        r = session.send(req)
        r.raise_for_status()
        if record is not None:
            record_fp.write(json.dumps(record) + "\n")
            record_fp.flush()


def delete_resources(args):
    """Returns 1 if any resource isn't in the registry."""
    n_failed = 0
    if args.dry_run:
        log.info("DRY RUN")

    record_ctx = open(args.record, "a") if args.record else nullcontext()
    with (
        open(args.resources) as fp,
        record_ctx as record_fp,
        util.registry_session(args.registry_url, auth=args.auth) as session,
    ):
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
                    record_fp=record_fp,
                )
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 404:
                    log.error("  - not in the registry, skipping")
                    n_failed += 1
                else:
                    raise err
    return 1 if n_failed else None


def update_hashes(args):
    """Returns 1 if any resource isn't in the registry, has no copy on this host,
    or couldn't be updated."""
    n_failed = 0
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
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        resources = util.query_registry_bulk(session, url, query)
        missing = set(to_update)
        for i, resource in enumerate(resources, start=1):
            missing.discard(resource["name"])
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
                    if not hasattr(location, "path"):
                        log.debug("    - %s is not local, skipping", loc)
                        continue
                    hash = util.hash(location.path)
                    if hash == resource["sha1"]:
                        log.info("    - already has the right hash")
                    elif args.dry_run:
                        log.info("    - would update hash to %s", hash)
                    else:
                        url, _params = registry.get_resource(
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
                    n_failed += 1
                    break
            else:
                log.error("    - no copy on this host, skipping")
                n_failed += 1
    for resource_id in sorted(missing):
        log.error(
            "- %s: not in the registry or has no locations, skipping", resource_id
        )
        n_failed += 1
    return 1 if n_failed else None


def supersede_resource(
    resource_id: str,
    *,
    session: httpx.Client,
    registry_url: str,
    by: str | None = None,
    note: str | None = None,
    clear: bool = False,
    dry_run: bool = False,
):
    """Mark a resource as superseded (by another resource, if by is given), or
    unmark it if clear. A resource that's already marked keeps the time it was
    marked, and only the details given change.

    Raises ValueError if clear is given with by or note.
    """
    if clear and (by is not None or note is not None):
        raise ValueError("clearing removes the replacement and note; don't give them")
    log.info("%s:", resource_id)
    url, _params = registry.get_resource(registry_url, resource_id)
    r = session.get(url)
    r.raise_for_status()
    record = r.json()
    if clear:
        data = {"superseded_on": None}
    else:
        data = {}
        if record["superseded_on"] is None:
            data["superseded_on"] = datetime.datetime.now(datetime.UTC).isoformat()
        if by is not None:
            data["superseded_by"] = by
        if note is not None:
            data["superseded_note"] = note
    if not data:
        log.info("  - already superseded; nothing to change")
        return
    if dry_run:
        log.info("  - would send %s", data)
        return
    r = session.patch(url, json=data)
    r.raise_for_status()
    updated = r.json()
    if updated["superseded_on"] is None:
        log.info("  - not superseded")
    else:
        log.info(
            "  - superseded on %s%s%s",
            updated["superseded_on"],
            f" by {updated['superseded_by']}" if updated["superseded_by"] else "",
            f" ({updated['superseded_note']})" if updated["superseded_note"] else "",
        )


def supersede_resources(args):
    """Returns 1 if any resource couldn't be marked."""
    n_failed = 0
    if args.dry_run:
        log.info("DRY RUN")
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        for resource_id in args.ids:
            try:
                supersede_resource(
                    resource_id,
                    session=session,
                    registry_url=args.registry_url,
                    by=args.by,
                    note=args.note,
                    clear=args.clear,
                    dry_run=args.dry_run,
                )
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 404:
                    log.error("  - not in the registry, skipping")
                else:
                    registry.log_error(err)
                n_failed += 1
            except ValueError as err:
                log.error("  - error: %s", err)
                return 1
    return 1 if n_failed else None


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
        default=None,
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
        "--record",
        type=Path,
        help="append the purged registry records to this file, as JSON lines",
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

    pp = sub.add_parser(
        "supersede",
        aliases=["supercede"],
        help="mark resources as superseded or not to be used",
        description="Mark resources as superseded by another resource (--by), "
        "or as not to be used (--note says why). The registry keeps the record, "
        "so the names stay valid. Needs a superuser.",
    )
    pp.set_defaults(func=supersede_resources)
    pp.add_argument(
        "-y",
        "--dry-run",
        help="if set, don't actually change anything",
        action="store_true",
    )
    group = pp.add_mutually_exclusive_group()
    group.add_argument("--by", metavar="ID", help="the resource to use instead")
    group.add_argument(
        "--clear",
        action="store_true",
        help="unmark the resources, clearing --by and --note",
    )
    pp.add_argument(
        "--note", help="why the resources shouldn't be used, or what replaces them"
    )
    pp.add_argument("ids", nargs="+", help="the resources to mark")

    args = p.parse_args()
    if not hasattr(args, "func"):
        p.print_usage()
        p.exit(0)
    if args.registry_url is None:
        p.error(
            f"supply a registry url with '-r' or {registry._env_registry} "
            "environment variable"
        )
    setup_log(log, args.debug)
    log.info("nbank admin version: %s", __version__)
    try:
        args.auth = core.make_auth(args.auth)
    except NetrcParseError as err:
        log.error("error: unable to use netrc file: %s", err.msg)
        raise SystemExit(1) from err
    # commands return 1 (or None for success), as nbank's do
    status = 1
    try:
        status = args.func(args) or 0
    except util.RegistryVersionError as err:
        log.error("error: %s", err)
    except httpx.RequestError:
        log.error("registry error: unable to contact server")
    except httpx.HTTPStatusError as err:
        registry.log_error(err)
    raise SystemExit(status)
