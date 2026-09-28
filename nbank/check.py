# -*- mode: python -*-
"""Integrity checks for archives and the registry

Copyright (C) 2026 Dan Meliza <dan@meliza.org>
"""

import enum
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from httpx import Client

from nbank import archive, registry, util


class Status(enum.Enum):
    OK = "OK"
    NOT_HASHED = "OK (no hash to verify)"
    UNREADABLE = "FAILED to read"
    HASH_MISMATCH = "FAILED to match hash"
    MISSING_FROM_ARCHIVE = "MISSING from the archive"
    MISSING_FROM_REGISTRY = "MISSING from the registry"


@dataclass(frozen=True)
class Finding:
    """The result of checking one resource."""

    status: Status
    resource: str
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status in (Status.OK, Status.NOT_HASHED)


def registry_resources_in_archive(
    session: Client, registry_url: str, archive_name: str
) -> dict[str, str | None]:
    """Returns {name: sha1} for the resources the registry places in archive_name."""
    url, _ = registry.find_resource(registry_url)
    return {
        item["name"]: item["sha1"]
        for item in util.query_registry_paginated(
            session, url, {"location": archive_name}
        )
    }


def check_archive_contents(
    archive_path: Path, expected: Mapping[str, str | None]
) -> Iterator[Finding]:
    """Compares the files in a local archive against expected resources.

    expected maps resource names to sha1 hashes (or None if the registry has no
    hash). Yields one Finding for each expected resource and one for each file
    in the archive that isn't expected. Doesn't contact the registry.
    """
    remaining = dict(expected)
    for resource_file in archive.iter_resources(archive_path):
        name = resource_file.stem
        try:
            sha1 = remaining.pop(name)
        except KeyError:
            yield Finding(Status.MISSING_FROM_REGISTRY, name, resource_file)
            continue
        if not os.access(resource_file, os.R_OK):
            status = Status.UNREADABLE
        elif sha1 is None:
            status = Status.NOT_HASHED
        elif util.hash(resource_file) != sha1:
            status = Status.HASH_MISMATCH
        else:
            status = Status.OK
        yield Finding(status, name, resource_file)
    for name in remaining:
        yield Finding(Status.MISSING_FROM_ARCHIVE, name)


__all__ = [
    "Finding",
    "Status",
    "check_archive_contents",
    "registry_resources_in_archive",
]
