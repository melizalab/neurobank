# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""
from pathlib import Path
from typing import Tuple, Dict, Iterator, Union, Optional, Any
import logging
import requests as rq

log = logging.getLogger("nbank")  # root logger


def deposit(
    archive_path: Path,
    files: Iterator[Path],
    dtype: str = None,
    hash: bool = False,
    auto_id: bool = False,
    auth: Union[Tuple[str], None] = None,
    **metadata: Any,
):
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
    from nbank.archive import get_config, store_resource, check_permissions
    from nbank.registry import add_resource, find_archive_by_path, full_url

    archive_cfg = get_config(archive_path)
    if archive_cfg is None:
        raise ValueError("%s is not a valid archive" % archive_path)
    archive_path = archive_cfg["path"]  # this will resolve the path
    log.info("archive: %s", archive_path)
    registry_url = archive_cfg["registry"]
    log.info("   registry: %s", registry_url)
    auto_id = archive_cfg["policy"]["auto_identifiers"] or auto_id
    auto_id_type = archive_cfg["policy"].get("auto_id_type", None)
    allow_dirs = archive_cfg["policy"]["allow_directories"]

    with rq.Session() as session:
        session.auth = auth
        # check that archive exists for this path
        url, params = find_archive_by_path(registry_url, archive_path)
        try:
            archive = util.query_registry(session, url, params)[0]["name"]
        except IndexError:
            raise RuntimeError(
                f"archive '{archive_path}' not in registry. did it move?"
            )
        except TypeError:
            raise ValueError(f"no archive list at {url}")
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
    from nbank.util import query_registry_paginated
    from nbank.registry import find_resource

    url, _ = find_resource(registry_url)
    with rq.Session() as session:
        return query_registry_paginated(session, url, params)


def describe(registry_url: str, id: str) -> Dict:
    """Returns the database record for id, or None if no match can be found"""
    from nbank.util import query_registry
    from nbank.registry import get_resource

    url, params = get_resource(registry_url, id)
    return query_registry(rq, url, params)


def find(
    registry_url: str, id: str, alt_base: Optional[Path] = None
) -> Iterator[Union[Path, str]]:
    """Generates a sequence of paths or URLs where id can be located

    Set alt_base to replace the dirname of any local resources. This is intended
    to be used with temporary copies of archives on other hosts.

    """
    from nbank.util import query_registry_paginated, parse_location
    from nbank.registry import get_locations

    url, params = get_locations(registry_url, id)
    with rq.Session() as session:
        for loc in query_registry_paginated(session, url, params):
            yield parse_location(loc, alt_base)


def get(registry_url: str, id: str, alt_base: Optional[Path] = None) -> Optional[Path]:
    """Returns the first path or URL where id can be found, or None if no match.

    If local_only is True, only files that can be found on the local filesystem
    are considered.

    Set alt_base to replace the dirname of any local resources. This is intended
    to be used with temporary copies of archives on other hosts.

    """
    try:
        return next(find(registry_url, id, alt_base))
    except StopIteration:
        pass


def verify(
    registry_url: str, file: Union[str, Path], id: str = None
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
            return resource["sha1"] == file_hash
        except TypeError:
            raise ValueError("%s does not exist" % id)


def fetch(base_url: str, id: str, target: Path) -> None:
    """Download the resource from the server and save as `target`.

    Raises ValueError if the resource does not exist or is not downloadable.
    Raises HTTPError on an error in the actual download.
    Raises FileExistsError if `target` already exists.

    """
    from nbank.util import download_to_file
    from nbank.registry import fetch_resource

    url, _ = fetch_resource(base_url, id)
    with rq.Session() as session:
        return download_to_file(session, url, target)


def update(
    base_url: str, *ids: str, auth: Union[Tuple[str], None] = None, **metadata: Any
) -> Dict:
    """Update metadata for one or more resources. Set a key to None to delete."""
    from nbank.registry import update_resource_metadata

    with rq.Session() as session:
        for id in ids:
            url, params = update_resource_metadata(base_url, id, **metadata)
            r = session.patch(
                url,
                json=params,
                auth=auth,
                headers={"Accept": "application/json"},
                verify=True,
            )
            if r.status_code == 404:
                yield {"name": id, "error": "not found"}
            r.raise_for_status()
            yield r.json()


# Variables:
# End:
