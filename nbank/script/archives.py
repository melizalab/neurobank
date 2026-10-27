# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""nbank archive subcommands: init, list, register tape and prune."""

import logging
import tarfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlunparse

import httpx

from nbank import archive, registry, transfer, util

log = logging.getLogger("nbank")


def init_archive(args):
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
                    # the archive is registered now, so go on with the others
                    log.error("  ✗ %s -> unable to add location", tarinfo.name)
                    registry.log_error(e)
                    n_failed += 1
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
            log.error("error: no archive '%s' in the registry", args.archive_name)
            return 1
        if result["scheme"] not in archive.Resource.schemes:
            log.error("error: '%s' is not a neurobank archive", args.archive_name)
            return 1
        if not Path(result["root"]).is_dir():
            log.error(
                "error: the archive '%s' is not on this host (%s)",
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
                resource = util.parse_location(locations[args.archive_name])
                if not isinstance(resource, archive.Resource):
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
                        log.error(
                            "  ✗ unable to remove from registry: %s",
                            registry.error_detail(r),
                        )
                        n_failed += 1
                        continue
                log.info("  - removed %s", req.url)
                if not args.dry_run:
                    resource.unlink()
                log.info("  - deleted %s", resource.path)
    return 1 if n_failed else None
