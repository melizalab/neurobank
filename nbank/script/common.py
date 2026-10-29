# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Helpers shared by the nbank command's subcommands."""

import logging
import sys
from functools import partial
from pathlib import Path

from nbank import (
    archive,
    core,
    registry,
    util,
)

log = logging.getLogger("nbank")


def records(session, registry_url: str, ids) -> dict[str, dict]:
    """Returns the registry records of the registered resources in ids, by id."""
    return {
        record["name"]: record
        for record in util.query_registry_bulk_batched(
            session, partial(registry.get_resource_bulk, registry_url), ids
        )
    }


def archive_config(path: Path) -> archive.ArchiveConfig | None:
    """Returns the configuration of the neurobank archive at path, or None (logging an
    error) if there isn't one."""
    try:
        return archive.ArchiveConfig.load(path)
    except FileNotFoundError:
        log.error("error: %s is not a valid neurobank archive", path)
        return None


def not_a_name(archive: str) -> bool:
    """Returns True (logging an error) if archive, given as a path or a name, isn't a
    directory but can't be an archive's name either."""
    if "/" in archive:
        log.error("error: %s is not a directory", archive)
        return True
    return False


def warn_other_registry(archive_cfg: archive.ArchiveConfig, registry_url: str) -> None:
    """Logs a warning if the archive's nbank.json names a registry other than
    registry_url, the one it's being checked against."""
    if archive_cfg.registry.rstrip("/") != registry_url.rstrip("/"):
        log.warning(
            " - warning: nbank.json points to a different registry (%s)",
            archive_cfg.registry,
        )


def registered_name(session, registry_url: str, archive_path: Path) -> str | None:
    """Returns the registry's name for the archive at archive_path, its real path, or
    None (logging an error) if it isn't registered."""
    url, params = registry.find_archive_by_path(registry_url, archive_path)
    info = util.query_registry_first(session, url, params)
    if info is None:
        log.error("error: %s", core.not_registered(archive_path))
        return None
    return info["name"]


def tar_lookup(session, registry_url, unregistered: list | None = None):
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


def stripped_lines(lines) -> list[str]:
    """Returns the lines that aren't blank, stripped of whitespace."""
    return [name for line in lines if (name := line.strip())]


def read_ids(path: Path) -> list[str]:
    """Returns the identifiers in a file, one per line, or in stdin if path is '-'."""
    if str(path) == "-":
        text = sys.stdin.read()
    else:
        text = path.read_text()
    return stripped_lines(text.splitlines())


def requested_ids(args) -> list[str] | None:
    """Returns the ids from the command line and --from-file.

    Returns None, after logging why, if there aren't any.
    """
    ids = list(args.ids)
    if args.from_file is not None:
        try:
            ids += read_ids(args.from_file)
        except OSError as err:
            log.error("error: unable to read %s: %s", args.from_file, err)
            return None
    if not ids:
        log.error("error: no identifiers given")
        return None
    return list(dict.fromkeys(ids))
