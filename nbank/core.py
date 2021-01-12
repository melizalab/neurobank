# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""functions for managing a data archive

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:52:28 2013
"""
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os
import logging

from nbank import util

log = logging.getLogger("nbank")  # root logger
env_registry = "NBANK_REGISTRY"


def default_registry():
    """Return the registry URL associated with the default registry environment variable """
    return os.environ.get(env_registry, None)


def parse_resource_id(id, base_url=None):
    """Parse a full or short resource identifier into base url and id.

    http://domain.name/app/resources/id/ -> (http://domain.name/app/, id)
    (id, "http://domain.name/app") -> ("http://domain.name/app/", id)
    (id, None) -> (default_registry(), id)
    """
    import re

    try:
        from urllib.parse import urlparse, urlunparse
    except ImportError:
        from urlparse import urlparse, urlunparse
    pr = urlparse(id)
    if pr.scheme and pr.netloc:
        m = re.match(r"(\S+?/)resources/([-_~0-9a-zA-Z]+)", pr.path)
        if m is None:
            raise ValueError("invalid neurobank resource URL")
        return (urlunparse(pr._replace(path=m.group(1))), m.group(2))
    if base_url is None:
        base_url = default_registry()
    return base_url, id


def full_url(id, base_url=None):
    """Returns the full URL of the resource"""
    base_url, id = parse_resource_id(id, base_url)
    if base_url is None:
        raise ValueError("short identifier supplied without a registry to resolve it")
    return "{}/resources/{}/".format(base_url.rstrip("/"), id)


def deposit(
    archive_path, files, dtype=None, hash=False, auto_id=False, auth=None, **metadata
):
    """Main entry point to deposit resources into an archive

    Yields the short IDs for each deposited item in files

    Here's how a variety of error conditions are handled:

    - unable to contact registry: ConnectionError
    - attempt to add unallowed directory: skip the directory
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
    from nbank.archive import get_config, store_resource
    from nbank.registry import add_resource, find_archive_by_path

    archive_path = os.path.abspath(archive_path)
    cfg = get_config(archive_path)
    if cfg is None:
        raise ValueError("%s is not a valid archive" % archive_path)
    log.info("archive: %s", archive_path)
    registry_url = cfg["registry"]
    log.info("   registry: %s", registry_url)
    auto_id = cfg["policy"]["auto_identifiers"] or auto_id
    auto_id_type = cfg["policy"].get("auto_id_type", None)
    allow_dirs = cfg["policy"]["allow_directories"]

    # check that archive exists for this path
    archive = find_archive_by_path(registry_url, archive_path)
    log.info("   archive name: %s", archive)
    if archive is None:
        raise RuntimeError("archive '%s' not in registry. did it move?" % archive_path)

    for src in files:
        log.info("processing '%s':", src)
        if not os.path.exists(src):
            log.info("   does not exist; skipping")
            continue
        if not allow_dirs and os.path.isdir(src):
            log.info("   is a directory; skipping")
            continue
        if auto_id:
            if auto_id_type == "uuid":
                id = str(uuid.uuid4())
            else:
                id = None
        else:
            id = util.id_from_fname(src)
        if hash or cfg["policy"]["require_hash"]:
            sha1 = util.hash(src)
            log.info("   sha1: %s", sha1)
        else:
            sha1 = None
        result = add_resource(registry_url, id, dtype, archive, sha1, auth, **metadata)
        id = full_url(result["name"], registry_url)
        log.info("   registered as %s", id)
        tgt = store_resource(cfg, src, id=result["name"])
        log.info("   deposited in %s", tgt)
        yield {"source": src, "id": result["name"]}


def search(registry_url=None, **params):
    """Searches the registry for resources that match query params, yielding a sequence of hits"""
    from nbank.registry import find_resource

    if registry_url is None:
        registry_url = default_registry()
    return find_resource(registry_url, **params)


def find(id, registry_url=None, local_only=False, alt_base=None):
    """Generates a sequence of paths or URLs where id can be located

    If local_only is True, only files that can be found on the local filesystem
    are yielded.

    Set alt_base to replace the dirname of any local resources. This is intended
    to be used with temporary copies of archives on other hosts.

    """
    from nbank.registry import get_locations
    from nbank.archive import find_resource

    base, sid = parse_resource_id(id, registry_url)
    if base is None:
        raise ValueError("short identifier supplied without a registry to resolve it")
    for loc in get_locations(base, sid):
        path = get_archive(loc, alt_base)
        if local_only:
            yield find_resource(path)
        else:
            yield path


def get(id, registry_url=None, local_only=False, alt_base=None):
    """Returns the first path or URL where id can be found, or None if no match.

    If local_only is True, only files that can be found on the local filesystem
    are considered.

    Set alt_base to replace the dirname of any local resources. This is intended
    to be used with temporary copies of archives on other hosts.

    """
    try:
        return next(find(id, registry_url, local_only, alt_base))
    except StopIteration:
        pass


def describe(id, registry_url=None):
    """Returns the database record for id, or None if no match can be found """
    from nbank.registry import get_resource

    base, sid = parse_resource_id(id, registry_url)
    return get_resource(base, sid)


def verify(file, registry_url=None, id=None):
    """Compute the hash for file and search the registry for any resource(s) associated with it.

    Returns a sequence of matching records. If id is not None, search instead by id
    and return True if the hash matches.

    """
    from nbank.registry import find_resource, get_resource

    if registry_url is None:
        registry_url = default_registry()
    if registry_url is None:
        raise ValueError("hash verification requires a registry")

    log.debug("verifying %s", file)
    file_hash = util.hash(file)
    if id is None:
        log.debug("  searching by hash (%s)", file_hash)
        return find_resource(registry_url, sha1=file_hash)
    else:
        log.debug("  searching by id (%s)", id)
        resource = get_resource(registry_url, id=id)
        try:
            return resource["sha1"] == file_hash
        except TypeError:
            raise ValueError("%s does not exist" % id)


def get_archive(location, alt_base=None):
    """Return the path or URL associated with location

    location is a dict with 'scheme', 'path', and 'resource_name' (like what's
    yielded by registry.get_locations). Note that for local (neurobank)
    locations, the resource may have an extension; this is not included.

    If alt_base is set, the dirname of the root will
    be replaced with this value; e.g. alt_base='/scratch' will change
    '/home/data/starlings' to '/scratch/starlings'. This is intended to be used
    with temporary copies of archives on other hosts.

    """
    import posixpath as pp
    from nbank.archive import resource_path

    try:
        from urllib.parse import urlunparse
    except ImportError:
        from urlparse import urlunparse
    root = location["root"]
    if location["scheme"] == "neurobank":
        if alt_base is not None:
            root = pp.join(alt_base, pp.basename(root))
        return resource_path(root, location["resource_name"])
    else:
        return urlunparse(
            (location["scheme"], root, location["resource_name"], "", "", "")
        )


# Variables:
# End:
