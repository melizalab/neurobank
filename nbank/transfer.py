# -*- mode: python -*-
"""Finding, copying, and verifying resources for transfer between archives

Copyright (C) 2026 Dan Meliza <dan@meliza.org>
"""

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from httpx import Client

from nbank import registry, util


@dataclass(frozen=True)
class Source:
    """A requested resource and where it can be read on this host.

    sha1 and filename come from the registry record. path is a readable local
    copy and archive the name of the archive that holds it; if there's no such
    copy, both are None and error says why.
    """

    id: str
    sha1: str | None = None
    filename: str | None = None
    path: Path | None = None
    archive: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


def _unreadable(path: Path) -> Path | None:
    """Returns path, or a file or directory inside it, that can't be read; else None."""
    if path.is_dir():
        for item in [path, *path.rglob("*")]:
            perm = (os.R_OK | os.X_OK) if item.is_dir() else os.R_OK
            if not os.access(item, perm):
                return item
        return None
    return None if os.access(path, os.R_OK) else path


def _find_local_copy(record: dict, alt_base: Path | None) -> Source:
    """Returns a Source for the first readable local copy among a record's locations."""
    fields = {
        "id": record["name"],
        "sha1": record.get("sha1"),
        "filename": record.get("filename"),
    }
    error = "no copy on this host"
    for location in record["locations"]:
        resource = util.parse_location(location, alt_base=alt_base)
        path = getattr(resource, "path", None)
        if path is None:
            continue
        unreadable = _unreadable(path)
        if unreadable is not None:
            error = f"'{unreadable}' is not readable"
            continue
        return Source(path=path, archive=location["archive_name"], **fields)
    return Source(error=error, **fields)


def find_sources(
    session: Client,
    registry_url: str,
    ids: Iterable[str],
    *,
    archive: str | None = None,
    alt_base: Path | None = None,
) -> Iterator[Source]:
    """Finds a readable copy on this host of each resource in ids.

    Yields one Source per distinct id, in the order requested. A Source without
    a path has an error saying why: the id isn't registered or has no locations
    (the registry doesn't distinguish these in a bulk request), or no location
    is on this host and readable. Set archive to use only copies in that archive.
    Only checks that files can be read; hashes are checked when they're copied.
    """
    ids = list(dict.fromkeys(ids))
    params = {} if archive is None else {"archive": archive}
    found = {}
    for i in range(0, len(ids), util.bulk_batch_size):
        url, query = registry.get_locations_bulk(
            registry_url, ids[i : i + util.bulk_batch_size], **params
        )
        for record in util.query_registry_bulk(session, url, query):
            found[record["name"]] = record
    for id in ids:
        record = found.get(id)
        if record is not None:
            yield _find_local_copy(record, alt_base)
        elif archive is not None:
            yield Source(id, error=f"not in archive '{archive}'")
        else:
            yield Source(id, error="not in the registry, or has no locations")


__all__ = ["Source", "find_sources"]
