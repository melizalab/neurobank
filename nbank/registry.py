# -*- mode: python -*-
"""Construct HTTP URLs and queries to the neurobank registry.

URL construction functions begin with `get_` if they retrieve a single record,
`find_` if they retrieve a sequence of records, `add_` if they create a new
record, and `update_` if they update an existing record. They all return a tuple
continaing the URL endpoint and a dictionary with the query parameters or
request body. `get_` and `find_` URLs should be used with the GET method; `add_`
URLs with the POST method; and `update_` URLs with the PATCH method.

"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, Union

_env_registry = "NBANK_REGISTRY"
_neurobank_scheme = "neurobank"
_local_schemes = (_neurobank_scheme,)
log = logging.getLogger("nbank")


def default_registry() -> Optional[str]:
    """Return the registry URL associated with the default registry environment
    variable, or None if the environment variable is not defined.
    """
    import os

    return os.environ.get(_env_registry)


def parse_resource_url(url: str) -> Tuple[str, str]:
    ""

    "Parse a full resource identifier into base url and id." ""
    import re
    from urllib.parse import urlparse, urlunparse

    pr = urlparse(url)
    if pr.scheme and pr.netloc:
        m = re.match(r"(\S+?/)resources/([-_~0-9a-zA-Z]+)/?$", pr.path)
        if m is None:
            raise ValueError("invalid neurobank resource URL")
        return (urlunparse(pr._replace(path=m.group(1))), m.group(2))
    else:
        raise ValueError("not a resource URL")


def full_url(base_url: str, id: str) -> str:
    """Returns the full URL of the resource"""
    return f"{base_url.rstrip('/')}/resources/{id}/"


def get_info(base_url: str) -> Tuple[str, None]:
    """Constructs URL to get registry information"""
    return (url_join(base_url, "info/"), None)


def get_datatypes(base_url: str) -> Tuple[str, None]:
    """Constructs URL to get known content type names"""
    return (url_join(base_url, "datatypes/"), None)


def get_archives(base_url: str, **params) -> Tuple[str, None]:
    """Constructs URL to get known archive names"""
    return (url_join(base_url, "archives/"), params)


def get_archive(base_url: str, name: str) -> Tuple[str, None]:
    """Constructs URL to get information about an archive by name"""
    return (url_join(base_url, "archives/", f"{name}/"), None)


def find_archive_by_path(base_url: str, path: Union[str, Path]) -> Tuple[str, Dict]:
    """Constructs URL to search for the archive associated with path"""
    return (
        url_join(base_url, "archives/"),
        {"scheme": _neurobank_scheme, "root": str(path)},
    )


def find_resource(base_url: str, **params) -> Tuple[str, Dict]:
    """Constructs URL to find resources that match params"""
    return (url_join(base_url, "resources/"), params)


def get_resource(base_url: str, id: str) -> Tuple[str, None]:
    """Constructs URL to retrieve registry record for id"""
    return (full_url(base_url, id), None)


def get_resource_bulk(base_url: str, ids: Sequence[str]) -> Tuple[str, Dict]:
    """Constructs URL to bulk retrieve registry records for ids"""
    return (url_join(base_url, "bulk", "resources/"), {"names": list(ids)})


def get_locations(base_url: str, id: str, **params) -> Tuple[str, Dict]:
    """Constructs URL to look up the locations of a resource."""
    return (url_join(base_url, "resources", id, "locations/"), params)


def get_locations_bulk(base_url: str, ids: Sequence[str], **params) -> Tuple[str, Dict]:
    """Constructs URL to bulk retrieve locations for multiple ids"""
    return (url_join(base_url, "bulk", "locations/"), {"names": list(ids), **params})


def add_location(base_url: str, id: str, archive: str) -> Tuple[str, Dict]:
    """Constructs URL to add a location for a resource (use post)"""
    return (
        url_join(base_url, "resources", id, "locations/"),
        {"archive_name": archive},
    )


def get_location(base_url: str, id: str, archive: str) -> Tuple[str, None]:
    """Construct URL to look up (or delete) a specific location"""
    return (url_join(base_url, "resources", id, "locations", f"{archive}/"), None)


def add_datatype(base_url: str, name: str, content_type: str) -> Tuple[str, Dict]:
    """Constructs URL to add a datatype to the registry"""
    return (
        url_join(base_url, "datatypes/"),
        {"name": name, "content_type": content_type},
    )


def add_archive(
    base_url: str, name: str, scheme: str, root: Union[Path, str]
) -> Tuple[str, Dict]:
    """Constructs URL to add an archive to the registry"""
    return (
        url_join(base_url, "archives/"),
        {"name": name, "scheme": scheme, "root": str(root)},
    )


def add_resource(
    base_url: str,
    id: Optional[str],
    dtype: Optional[str],
    archive: Optional[str],
    sha1: Optional[str] = None,
    **metadata: Any,
) -> Tuple[str, Dict]:
    """Constructs URL to add a resource to the registry"""
    url = url_join(base_url, "resources/")
    data = {
        "name": id,
        "dtype": dtype,
        "sha1": sha1,
        "locations": [archive],
        "metadata": metadata,
    }
    return (url, strip_nulls(data))


def update_resource_metadata(base_url: str, id: str, **metadata) -> Tuple[str, Dict]:
    """Constructs URL to update metadata in the registry. Set a key to None to delete"""
    return (full_url(base_url, id), {"metadata": metadata})


def url_join(base: str, *path: str) -> str:
    """Construct a URL by joining parts to a base"""
    import posixpath as pp
    from urllib.parse import urlparse, urlunparse

    if any(p.startswith("/") for p in path):
        raise ValueError("components of the path must not start with a slash")
    parts = urlparse(base)
    return urlunparse(parts._replace(path=pp.join(parts.path, *path)))


def strip_nulls(d: Dict) -> Dict:
    """Removes all keys from a dict that are equal to None"""
    return {k: v for k, v in d.items() if v is not None}


def log_error(err):
    """Writes error message from server to log. Reraises errors where code is not in 400, 403, 415"""
    if err.response.status_code == 400:
        data = err.response.json()
        for k, v in data.items():
            for vv in v:
                log.error("   registry error: %s: %s", k, vv)
    elif err.response.status_code == 403:
        data = err.response.json()
        for _k, v in data.items():
            log.error("   registry error: %s", v)
    elif err.response.status_code == 415:
        log.error("    registry error: %s", err.response.reason_phrase)
    else:
        raise err


def local_schemes() -> Tuple[str]:
    return _local_schemes


__all__ = [
    "add_archive",
    "add_datatype",
    "add_resource",
    "default_registry",
    "find_archive_by_path",
    "find_resource",
    "full_url",
    "get_archives",
    "get_datatypes",
    "get_info",
    "get_locations",
    "get_locations_bulk",
    "get_resource",
    "get_resource_bulk",
    "local_schemes",
    "log_error",
    "parse_resource_url",
    "update_resource_metadata",
]
