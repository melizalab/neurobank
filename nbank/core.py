# -*- mode: python -*-
"""core functions for managing data registry and archives

Copyright (C) 2013-2025 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple, Union

import httpx

from nbank.util import FetchableResource

# types that can be turned into authentication for httpx
RegistryAuth = Union[Tuple[str, str], httpx.Auth, None]

log = logging.getLogger("nbank")  # root logger


def make_auth(auth: RegistryAuth) -> Optional[httpx.Auth]:
    """Convert a RegistryAuth to an actual httpx Auth. If auth is None, tries to use netrc"""
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
    dtype: Optional[str] = None,
    hash: bool = False,
    auto_id: bool = False,
    auth: RegistryAuth = None,
    **metadata: Any,
) -> Iterator[Dict]:
    """Main entry point to deposit resources into an archive

    Yields the short IDs for each deposited item in files

    Here's how a variety of error conditions are handled:

    - unable to contact registry: ConnectionError
    - attempt to add unallowed directory: skip the directory
    - unable to write to target directory: OSError
    - failed to register resource for any reason: HTTPError, usually 400 error code
    - unable to match archive path to archive in registry: RuntimeError
    - failed to add the file (usually b/c the identifier is taken): RuntimeError

    The last two errors indicate a major problem where the archive has perhaps
    been moved, or the archive in the registry is not pointing to the right
    location, or data has been stored in the archive without contacting the
    registry. These are currently hairy enough problems that the user is going
    to have to fix them herself for now.

    """
    import uuid

    from nbank import util
    from nbank.archive import check_permissions, get_config, store_resource
    from nbank.registry import add_resource, find_archive_by_path, full_url

    try:
        archive_cfg = get_config(archive_path)
    except FileNotFoundError as err:
        raise ValueError(f"{archive_path} is not a valid archive") from err
    archive_path = archive_cfg["path"]  # this will resolve the path
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

        for src in files:
            log.info("processing '%s':", src)
            if not src.exists():
                log.info("   does not exist; skipping")
                continue
            if not allow_dirs and src.is_dir():
                log.info("   is a directory; skipping")
                continue
            if auto_id:
                if auto_id_type == "uuid":
                    id = str(uuid.uuid4())
                else:
                    id = None
            else:
                id = util.id_from_fname(src)
            if not check_permissions(archive_cfg, src, id):
                raise OSError("unable to write to archive, aborting")
            if hash or archive_cfg["policy"]["require_hash"]:
                sha1 = util.hash(src)
                log.info("   sha1: %s", sha1)
            else:
                sha1 = None
            url, params = add_resource(
                registry_url, id, dtype, archive, sha1, **metadata
            )
            log.debug("POST %s: %s", url, params)
            r = session.post(url, json=params)
            r.raise_for_status()
            result = r.json()

            log.info("   registered as %s", full_url(registry_url, result["name"]))
            tgt = store_resource(archive_cfg, src, id=result["name"])
            log.info("   deposited in %s", tgt)
            yield {"source": src, "id": result["name"]}


def search(registry_url: str, **params) -> Iterator[Dict]:
    """Searches the registry for resources that match query params, yielding a sequence of hits"""
    from nbank.registry import find_resource
    from nbank.util import query_registry_paginated

    url, _ = find_resource(registry_url)
    with httpx.Client() as session:
        yield from query_registry_paginated(session, url, params)


def describe(registry_url: str, id: str) -> Optional[Dict]:
    """Returns the database record for a resource, or None if it does not exist in the registry"""
    from nbank.registry import get_resource
    from nbank.util import query_registry

    url, params = get_resource(registry_url, id)
    with httpx.Client() as session:
        return query_registry(session, url, params)


def describe_many(registry_url: str, *ids: str) -> Iterator[Dict]:
    """Returns the database record(s) for one or more resources.

    Yields one record for each resource that was located in the registry.

    """
    from nbank.registry import get_resource_bulk
    from nbank.util import query_registry_bulk

    url, query = get_resource_bulk(registry_url, ids)
    with httpx.Client() as session:
        yield from query_registry_bulk(session, url, query)


def find(
    registry_url: str, id: str, alt_base: Optional[Path] = None
) -> Iterator[FetchableResource]:
    """Generates a sequence of Fetchables where id can be located

    Set alt_base to replace the dirname of any local resources. This is intended
    to be used with temporary copies of archives on other hosts.

    """
    from nbank.registry import get_locations
    from nbank.util import parse_location, query_registry_paginated

    url, params = get_locations(registry_url, id)
    with httpx.Client() as session:
        for loc in query_registry_paginated(session, url, params):
            yield parse_location(loc, alt_base=alt_base, http_session=session)


def get(
    registry_url: str, id: str, alt_base: Optional[Path] = None
) -> Optional[FetchableResource]:
    """Returns the first path or URL where id can be found, or None if no match.

    Set alt_base to replace the dirname of any local resources. This is intended
    to be used with temporary copies of archives on other hosts.

    """
    try:
        return next(find(registry_url, id, alt_base))
    except StopIteration:
        pass


def verify(
    registry_url: str, file: Union[str, Path], id: Optional[str] = None
) -> Union[Iterator[Dict], bool]:
    """Compute the hash for file and search the registry for any resource(s) associated with it.

    Returns a sequence of matching records. If id is not None, search instead by id
    and return True if the hash matches.

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


def fetch(
    base_url: str,
    id: str,
    target: Path,
    *,
    auth: RegistryAuth = None,
) -> None:
    """Download the resource from the server and save as `target`.

    Raises ValueError if the resource does not exist or is not downloadable.
    Raises HTTPError on an error in the actual download.
    Raises FileExistsError if `target` already exists.

    """
    from nbank.registry import get_locations
    from nbank.util import download_to_file, parse_location, query_registry

    # query the database for the URL
    url, _ = get_locations(base_url, id)
    with httpx.Client() as session:
        session.auth = make_auth(auth)
        for loc in query_registry(session, url):
            if loc["scheme"] in ("https", "http"):
                res_url = parse_location(loc)
                log.info("fetching %s → %s", res_url, target)
                return download_to_file(session, res_url, target)
        raise ValueError(f"resource '{id}' does not exist or is not downloadable")


def update(
    base_url: str, *ids: str, auth: RegistryAuth = None, **metadata: Any
) -> Iterator[Dict]:
    """Update metadata for one or more resources. Set a key to None to delete."""
    from nbank.registry import update_resource_metadata

    with httpx.Client(headers={"Accept": "application/json"}) as session:
        session.auth = make_auth(auth)
        for id in ids:
            url, params = update_resource_metadata(base_url, id, **metadata)
            r = session.patch(url, json=params)
            if r.status_code == 404:
                yield {"name": id, "error": "not found"}
            r.raise_for_status()
            yield r.json()


__all__ = [
    "deposit",
    "describe",
    "describe_many",
    "fetch",
    "find",
    "get",
    "search",
    "update",
    "verify",
]

# Variables:
# End:
