# -*- mode: python -*-
"""Integrity checks for archives and the registry

Copyright (C) 2026 Dan Meliza <dan@meliza.org>
"""

import enum
import os
from collections import defaultdict
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
    MISPLACED = "in the WRONG subdirectory"
    DUPLICATE = "DUPLICATE file for the same resource"
    UNEXPECTED = "UNEXPECTED file outside a resource subdirectory"


@dataclass(frozen=True)
class Finding:
    """The result of checking one resource or file."""

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
    # the registry's location filter is a substring match, so filter again here
    # TODO: remove this if the registry is patched to support exact matches
    return {
        item["name"]: item["sha1"]
        for item in util.query_registry_paginated(
            session, url, {"location": archive_name}
        )
        if archive_name in item["locations"]
    }


def _scan_archive(archive_path: Path) -> tuple[dict[str, list[Path]], list[Path]]:
    """Returns ({resource name: [files]}, [unexpected files]) for an archive."""
    files = defaultdict(list)
    unexpected = []
    for entry in sorted((archive_path / archive._resource_subdir).iterdir()):
        if not entry.is_dir():
            unexpected.append(entry)
            continue
        for resource_file in sorted(entry.iterdir()):
            files[resource_file.stem].append(resource_file)
    return files, unexpected


def check_archive_contents(
    archive_path: Path, expected: Mapping[str, str | None]
) -> Iterator[Finding]:
    """Compares the files in a local archive against expected resources.

    expected maps resource names to sha1 hashes (or None if the registry has no
    hash). Yields one Finding for each expected resource, one for each file in
    the archive that isn't expected, one for each file that shares a resource
    name with another file, and one for each file sitting directly in the
    resources directory. Doesn't contact the registry.
    """
    remaining = dict(expected)
    files, unexpected = _scan_archive(archive_path)
    for path in unexpected:
        yield Finding(Status.UNEXPECTED, path.name, path)
    for name, paths in files.items():
        if name not in remaining:
            for path in paths:
                yield Finding(Status.MISSING_FROM_REGISTRY, name, path)
            continue
        sha1 = remaining.pop(name)
        if len(paths) > 1:
            for path in paths:
                yield Finding(Status.DUPLICATE, name, path)
            continue
        path = paths[0]
        if path.parent.name != archive.id_stub(name):
            status = Status.MISPLACED
        elif not os.access(path, os.R_OK):
            status = Status.UNREADABLE
        elif sha1 is None:
            status = Status.NOT_HASHED
        elif util.hash(path) != sha1:
            status = Status.HASH_MISMATCH
        else:
            status = Status.OK
        yield Finding(status, name, path)
    for name in remaining:
        yield Finding(Status.MISSING_FROM_ARCHIVE, name)


__all__ = [
    "Finding",
    "Status",
    "check_archive_contents",
    "registry_resources_in_archive",
]
