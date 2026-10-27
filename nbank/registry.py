# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Construct HTTP URLs and queries to the neurobank registry.

None of the functions in this module actually interact with the registry. They return
values that can be passed to an HTTP client library like httpx or requests.

URL construction functions begin with `get_` if they retrieve a single record,
`find_` if they retrieve a sequence of records, `add_` if they create a new
record, and `update_` if they update an existing record. They all return a tuple
containing the URL endpoint and a dictionary with the query parameters or
request body. `get_` and `find_` URLs should be used with the GET method; `add_`
URLs with the POST method; and `update_` URLs with the PATCH method.

"""

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_env_registry = "NBANK_REGISTRY"
_neurobank_scheme = "neurobank"
_local_schemes = (_neurobank_scheme,)
# resource fields that callers pass along with metadata
core_fields = ("experimenter", "animal")
log = logging.getLogger("nbank")


def default_registry() -> str | None:
    """Return the registry URL associated with the default registry environment
    variable, or None if the environment variable is not defined.
    """
    import os

    return os.environ.get(_env_registry)


def parse_resource_url(url: str) -> tuple[str, str]:
    """Returns (base url, id) for a full resource URL. Raises ValueError if url
    isn't one."""
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


def get_info(base_url: str) -> tuple[str, None]:
    """Constructs URL to get registry information"""
    return (url_join(base_url, "info/"), None)


def get_datatypes(base_url: str) -> tuple[str, None]:
    """Constructs URL to get known content type names"""
    return (url_join(base_url, "datatypes/"), None)


def get_archives(base_url: str, /, **params) -> tuple[str, dict]:
    """Constructs URL to get known archive names"""
    return (url_join(base_url, "archives/"), params)


def get_archive(base_url: str, name: str) -> tuple[str, None]:
    """Constructs URL to get information about an archive by name"""
    return (url_join(base_url, "archives/", f"{name}/"), None)


def find_archive_by_path(base_url: str, path: str | Path) -> tuple[str, dict]:
    """Constructs URL to search for the archive associated with path"""
    return (
        url_join(base_url, "archives/"),
        {"scheme": _neurobank_scheme, "root": str(path)},
    )


def find_resource(base_url: str, /, **params) -> tuple[str, dict]:
    """Constructs URL to find resources that match params"""
    return (url_join(base_url, "resources/"), params)


def hash_search_param(value: str) -> dict:
    """Returns the query parameter to search the registry for a full or partial hash.

    A full sha1 is an exact match, which the registry can look up quickly; part
    of a hash needs the slower substring filter.
    """
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return {"sha1": value}
    return {"sha1_contains": value}


def find_locations(base_url: str, /, **params) -> tuple[str, dict]:
    """Constructs URL to find locations that match params"""
    return (url_join(base_url, "locations/"), params)


def get_resource(base_url: str, id: str) -> tuple[str, None]:
    """Constructs URL to retrieve registry record for id"""
    return (full_url(base_url, id), None)


def get_resource_bulk(base_url: str, ids: Sequence[str]) -> tuple[str, dict]:
    """Constructs URL to bulk retrieve registry records for ids"""
    return (url_join(base_url, "bulk", "resources/"), {"names": list(ids)})


def get_locations(base_url: str, id: str, /, **params) -> tuple[str, dict]:
    """Constructs URL to look up the locations of a resource."""
    return (url_join(base_url, "resources", id, "locations/"), params)


def get_locations_bulk(
    base_url: str, ids: Sequence[str], /, **params
) -> tuple[str, dict]:
    """Constructs URL to bulk retrieve locations for multiple ids"""
    return (url_join(base_url, "bulk", "locations/"), {"names": list(ids), **params})


def add_location(
    base_url: str, id: str, archive: str, key: str | None = None
) -> tuple[str, dict]:
    """Constructs URL to add a location for a resource (use post).

    key records where the resource is within the archive, if that can't be
    derived from its id.
    """
    return (
        url_join(base_url, "resources", id, "locations/"),
        strip_nulls({"archive_name": archive, "key": key}),
    )


def update_location(
    base_url: str, id: str, archive: str, key: str | None
) -> tuple[str, dict]:
    """Constructs URL to set (or, with None, clear) the key of a location (use patch)"""
    return (
        url_join(base_url, "resources", id, "locations", f"{archive}/"),
        {"key": key},
    )


def get_location(base_url: str, id: str, archive: str) -> tuple[str, None]:
    """Construct URL to look up (or delete) a specific location"""
    return (url_join(base_url, "resources", id, "locations", f"{archive}/"), None)


def add_datatype(base_url: str, name: str, content_type: str) -> tuple[str, dict]:
    """Constructs URL to add a datatype to the registry"""
    return (
        url_join(base_url, "datatypes/"),
        {"name": name, "content_type": content_type},
    )


def add_archive(
    base_url: str, name: str, scheme: str, root: Path | str, **kwargs: str
) -> tuple[str, dict]:
    """Constructs URL to add an archive to the registry.

    kwargs are other fields of the archive record, such as accessibility.
    """

    return (
        url_join(base_url, "archives/"),
        dict(name=name, scheme=scheme, root=str(root), **kwargs),
    )


def add_resource(
    base_url: str,
    id: str | None,
    dtype: str | None,
    archive: str | None,
    sha1: str | None = None,
    key: str | None = None,
    /,
    **metadata: Any,
) -> tuple[str, dict]:
    """Constructs URL to add a resource to the registry.

    key records where the resource is within the archive (see add_location). The
    core fields (experimenter, animal) in metadata are sent as fields of the
    resource.
    """
    url = url_join(base_url, "resources/")
    fields, metadata = _split_core_fields(metadata)
    location = archive if key is None else {"archive": archive, "key": key}
    data = {
        "name": id,
        "dtype": dtype,
        "sha1": sha1,
        "locations": [location],
        **fields,
        "metadata": metadata,
    }
    return (url, strip_nulls(data))


def update_resource_metadata(base_url: str, id: str, /, **metadata) -> tuple[str, dict]:
    """Constructs URL to update metadata in the registry. Set a key to None to delete.

    The core fields (experimenter, animal) in metadata are sent as
    fields of the resource; None clears them.
    """
    fields, metadata = _split_core_fields(metadata)
    return (full_url(base_url, id), {**fields, "metadata": metadata})


def _split_core_fields(metadata: dict) -> tuple[dict, dict]:
    """Returns ({core field: value}, other metadata)"""
    fields = {k: v for k, v in metadata.items() if k in core_fields}
    return fields, {k: v for k, v in metadata.items() if k not in core_fields}


def url_join(base: str, *path: str) -> str:
    """Construct a URL by joining parts to a base"""
    import posixpath as pp
    from urllib.parse import urlparse, urlunparse

    if any(p.startswith("/") for p in path):
        raise ValueError("components of the path must not start with a slash")
    parts = urlparse(base)
    return urlunparse(parts._replace(path=pp.join(parts.path, *path)))


def strip_nulls(d: dict) -> dict:
    """Removes all keys from a dict that are equal to None"""
    return {k: v for k, v in d.items() if v is not None}


def check_resources(base_url: str, bodies: Sequence[dict]) -> tuple[str, list]:
    """Constructs URL to check resources as they would be added (bodies from
    add_resource), without adding them"""
    return (url_join(base_url, "check", "resources/"), list(bodies))


def field_messages(errors: dict) -> list[str]:
    """Returns the messages in the registry's errors for a resource, as 'field:
    message'"""
    messages = []
    for k, v in errors.items():
        # a field has a list of messages; "detail" has a single string
        for message in [v] if isinstance(v, str) else v:
            messages.append(f"{k}: {message}")
    return messages


def error_messages(response) -> list[str]:
    """Returns the messages in a 400 (validation error) response from the registry."""
    return field_messages(response.json())


def error_detail(response) -> str:
    """Returns the messages in an error response from the registry as one string, or
    the response's text if it isn't a validation error."""
    try:
        return "; ".join(error_messages(response))
    except ValueError:
        return response.text


def log_error(err):
    """Logs the registry's message for 400, 403, and 415 errors; re-raises others."""
    if err.response.status_code == 400:
        for message in error_messages(err.response):
            log.error("   registry error: %s", message)
    elif err.response.status_code == 403:
        data = err.response.json()
        for _k, v in data.items():
            log.error("   registry error: %s", v)
    elif err.response.status_code == 415:
        log.error("    registry error: %s", err.response.reason_phrase)
    else:
        raise err


def local_schemes() -> tuple[str]:
    """Returns the schemes of archives that are directories on a host."""
    return _local_schemes


__all__ = [
    "add_archive",
    "add_datatype",
    "add_resource",
    "default_registry",
    "find_archive_by_path",
    "find_locations",
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
