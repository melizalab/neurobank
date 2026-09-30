# -*- mode: python -*-
"""core functions for managing data registry and archives

Copyright (C) 2013-2025 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx

from nbank.util import FetchableResource

# types that can be turned into authentication for httpx
RegistryAuth = tuple[str, str] | httpx.Auth | None

log = logging.getLogger("nbank")  # root logger


def make_auth(auth: RegistryAuth) -> httpx.Auth | None:
    """Converts a RegistryAuth to an httpx Auth, using .netrc if auth is None."""
    if isinstance(auth, httpx.Auth):
        return auth
    if isinstance(auth, tuple):
        return httpx.BasicAuth(*auth)
    try:
        return httpx.NetRCAuth()
    except FileNotFoundError:
        pass


def deposit(
    archive_path: Path,
    files: Iterable[Path],
    dtype: str | None = None,
    hash: bool = False,
    auto_id: bool = False,
    auth: RegistryAuth = None,
    dry_run: bool = False,
    skip_errors: bool = False,
    **metadata: Any,
) -> Iterator[dict]:
    """Registers files and moves them into the archive at archive_path.

    dtype is the registered datatype of the files. hash registers each file's sha1 even
    if the archive doesn't require it. auto_id (or the archive's auto_identifiers
    policy) gives each file a new id, a UUID if the policy's auto_id_type is "uuid" and
    otherwise assigned by the registry, instead of using the file's name. auth is for
    the registry (default: .netrc). metadata is stored with each resource.

    Yields {"source": path, "id": id} for each file deposited. With dry_run, checks
    everything but registers and moves nothing; records have "dry_run": True, and "id"
    is None if the registry would assign it. With skip_errors, a file that can't be
    deposited yields {"source": path, "error": message} instead of raising. That covers
    missing files, directories the archive doesn't allow, invalid names, symbolic links,
    permission problems, and resources the registry rejects (400).

    Otherwise, directories the archive doesn't allow are skipped with a log message, and
    these errors are raised:

    - ValueError: archive_path isn't an archive, or a source is or has a symbolic link
    - RuntimeError: the archive or dtype isn't registered
    - PermissionError: a source can't be read or the archive can't be written
    - httpx.HTTPStatusError: the registry refuses a resource
    - httpx.ConnectError: the registry can't be reached
    - KeyError: a file is already stored for an id (the archive and registry disagree)
    """
    import uuid

    from nbank import util
    from nbank.archive import (
        get_config,
        store_resource,
        verify_no_symlinks,
        verify_permissions,
    )
    from nbank.registry import (
        add_resource,
        error_messages,
        find_archive_by_path,
        full_url,
        get_datatypes,
    )

    try:
        archive_cfg = get_config(archive_path)
    except FileNotFoundError as err:
        raise ValueError(f"{archive_path} is not a valid archive") from err
    archive_path = archive_cfg["path"]  # this will resolve the path
    if dry_run:
        log.info("DRY RUN: no changes will be made")
    log.info("archive: %s", archive_path)
    registry_url = archive_cfg["registry"]
    log.info("   registry: %s", registry_url)
    auto_id = archive_cfg["policy"]["auto_identifiers"] or auto_id
    auto_id_type = archive_cfg["policy"].get("auto_id_type", None)
    allow_dirs = archive_cfg["policy"]["allow_directories"]

    with httpx.Client() as session:
        session.auth = make_auth(auth)
        # check that archive exists for this path
        url, params = find_archive_by_path(registry_url, archive_path)
        try:
            archive = util.query_registry_first(session, url, params)["name"]
        except TypeError as err:
            raise RuntimeError(
                f"archive '{archive_path}' not in registry. did it move?"
            ) from err
        log.info("   archive name: %s", archive)

        # check the dtype before hashing any files
        if dtype is not None:
            url, params = get_datatypes(registry_url)
            known_dtypes = {
                d["name"] for d in util.query_registry_paginated(session, url, params)
            }
            if dtype not in known_dtypes:
                raise RuntimeError(f"'{dtype}' is not a registered datatype")

        def skipped(src: Path, message: str) -> dict:
            log.error("   error: %s", message)
            return {"source": src, "error": message}

        for src in files:
            log.info("processing '%s':", src)
            if not src.exists():
                if skip_errors:
                    yield skipped(src, "does not exist")
                else:
                    log.info("   does not exist; skipping")
                continue
            if not allow_dirs and src.is_dir():
                if skip_errors:
                    yield skipped(
                        src, "is a directory, which the archive doesn't allow"
                    )
                else:
                    log.info("   is a directory; skipping")
                continue
            try:
                if auto_id:
                    if auto_id_type == "uuid":
                        id = str(uuid.uuid4())
                    else:
                        id = None
                else:
                    id = util.id_from_fname(src)
                verify_no_symlinks(src)
                verify_permissions(archive_cfg, src, id)
                if hash or archive_cfg["policy"]["require_hash"]:
                    sha1 = util.hash(src)
                    log.info("   sha1: %s", sha1)
                else:
                    sha1 = None
            except (ValueError, OSError) as err:
                if not skip_errors:
                    raise
                yield skipped(src, str(err))
                continue
            if dry_run:
                log.info("   OK (dry run; nothing registered or moved)")
                yield {"source": src, "id": id, "dry_run": True}
                continue
            url, params = add_resource(
                registry_url, id, dtype, archive, sha1, **metadata
            )
            log.debug("POST %s: %s", url, params)
            r = session.post(url, json=params)
            if skip_errors and r.status_code == httpx.codes.BAD_REQUEST:
                yield skipped(src, "; ".join(error_messages(r)))
                continue
            r.raise_for_status()
            result = r.json()

            log.info("   registered as %s", full_url(registry_url, result["name"]))
            tgt = store_resource(archive_cfg, src, id=result["name"])
            log.info("   deposited in %s", tgt)
            yield {"source": src, "id": result["name"]}


def search(registry_url: str, **params) -> Iterator[dict]:
    """Yields the registry records that match the query params."""
    from nbank.registry import find_resource
    from nbank.util import query_registry_paginated

    url, _ = find_resource(registry_url)
    with httpx.Client() as session:
        yield from query_registry_paginated(session, url, params)


def describe(registry_url: str, id: str) -> dict | None:
    """Returns the registry record for id, or None if it isn't registered."""
    from nbank.registry import get_resource
    from nbank.util import query_registry

    url, params = get_resource(registry_url, id)
    with httpx.Client() as session:
        return query_registry(session, url, params)


def describe_many(registry_url: str, *ids: str) -> Iterator[dict]:
    """Yields the registry record of each resource in ids that's registered."""
    from nbank.registry import get_resource_bulk
    from nbank.util import query_registry_bulk

    url, query = get_resource_bulk(registry_url, ids)
    with httpx.Client() as session:
        yield from query_registry_bulk(session, url, query)


def find(
    registry_url: str, id: str, alt_base: Path | None = None
) -> Iterator[FetchableResource | None]:
    """Yields a Fetchable for each location of id (None if this host can't reach it).

    An empty sequence means the resource has no locations. Raises HTTPStatusError (404)
    if id isn't registered. alt_base, if given, replaces the directory that contains
    each archive, for copies of archives on another host (see archive.Resource).

    Deprecated: fetch() doesn't work for http(s) locations, as the session is closed,
    and will raise NotFetchableError in a future release. Use `nbank fetch` to download.
    """
    # TODO: consider a more transparent readout for unreachable/nonexistent conditions
    from nbank.registry import get_locations
    from nbank.util import parse_location, query_registry_paginated

    url, params = get_locations(registry_url, id)
    with httpx.Client() as session:
        for loc in query_registry_paginated(session, url, params):
            yield parse_location(loc, alt_base=alt_base, http_session=session)


def get(
    registry_url: str, id: str, alt_base: Path | None = None
) -> FetchableResource | None:
    """Returns the first path or URL for id that this host can reach, or None.

    Raises HTTPStatusError (404) if id isn't registered. alt_base, if given, replaces
    the directory that contains each archive, for copies of archives on another host
    (see archive.Resource).
    """
    # TODO: consider a more transparent readout for unreachable/nonexistent conditions
    for resource in find(registry_url, id, alt_base):
        if resource is not None:
            return resource


def verify(
    registry_url: str, file: str | Path, id: str | None = None
) -> Iterator[dict] | bool:
    """Hashes file and returns the registry records with that hash.

    If id is given, returns whether the hash matches id's record instead, and raises
    ValueError if id isn't registered.
    """
    from nbank.util import hash

    log.debug("verifying %s", file)
    file_hash = hash(file)
    if id is None:
        log.debug("  searching by hash (%s)", file_hash)
        return search(registry_url, sha1=file_hash)
    else:
        log.debug("  searching by id (%s)", id)
        resource = describe(registry_url, id=id)
        try:
            log.debug("  registry: %s; file: %s", resource["sha1"], file_hash)
            return resource["sha1"] == file_hash
        except TypeError as err:
            raise ValueError(f"{id} does not exist") from err


def update(
    base_url: str, *ids: str, auth: RegistryAuth = None, **metadata: Any
) -> Iterator[dict]:
    """Update metadata for one or more resources. Set a key to None to delete.

    Yields the updated record for each resource. A resource that is not in the
    registry yields {"name": id, "error": "not found"}.
    """
    from nbank.registry import update_resource_metadata

    with httpx.Client(headers={"Accept": "application/json"}) as session:
        session.auth = make_auth(auth)
        for id in ids:
            url, params = update_resource_metadata(base_url, id, **metadata)
            r = session.patch(url, json=params)
            if r.status_code == 404:
                yield {"name": id, "error": "not found"}
                continue
            r.raise_for_status()
            yield r.json()


__all__ = [
    "deposit",
    "describe",
    "describe_many",
    "find",
    "get",
    "search",
    "update",
    "verify",
]

# Variables:
# End:
