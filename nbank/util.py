# -*- mode: python -*-
"""utility functions

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Tue Jul  8 14:23:35 2014
"""
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, NewType, Optional, Union

ResourceLocation = NewType("ResourceLocation", Union[Path, str])


def id_from_fname(fname: Union[Path, str]) -> str:
    """Generates an ID from the basename of fname, stripped of any extensions.

    Raises ValueError unless the resulting id only contains URL-unreserved characters
    ([-_~0-9a-zA-Z])
    """
    import re

    id = Path(fname).stem
    if re.match(r"^[-_~0-9a-zA-Z]+$", id) is None:
        raise ValueError("resource name '%s' contains invalid characters", id)
    return id


def hash(fname: Union[Path, str], method: str = "sha1") -> str:
    """Returns a hash of the contents of fname using method.

    fname can be the path to a regular file or a directory.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib

    p = Path(fname).resolve(strict=True)
    block_size = 65536
    if p.is_dir():
        return hash_directory(p, method)
    hash = hashlib.new(method)
    with open(p, "rb") as fp:
        while True:
            data = fp.read(block_size)
            if not data:
                break
            hash.update(data)
    return hash.hexdigest()


def hash_directory(path: Union[Path, str], method: str = "sha1") -> str:
    """Return hash of the contents of the directory at path using method.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib

    p = Path(path).resolve(strict=True)
    hashes = []
    for fn in sorted(p.rglob("*")):
        with open(fn, "rb") as fp:
            hashes.append(f"{fn}={hashlib.new(method, fp.read()).hexdigest()}")
    return hashlib.new(method, "\n".join(hashes).encode("utf-8")).hexdigest()


def parse_location(
    location: Mapping[str, str], alt_base: Union[Path, str, None] = None
) -> ResourceLocation:
    """Return the path or URL associated with location

    location is a dict with 'scheme', 'root', and 'resource_name'.

    If scheme is "neurobank", the root field is interpreted as being the path of
    a neurobank archive on the local file system. If the `alt_base` parameter is
    set, the dirname of the root will be replaced with this value; e.g.
    alt_base='/scratch' will change '/home/data/starlings' to
    '/scratch/starlings'. This is intended to be used with temporary copies of
    archives on other hosts.

    All other schemes are interpreted as schemes in network URLs.

    """
    from urllib.parse import urlunparse

    from nbank.archive import resource_path
    from nbank.registry import _local_schemes

    root = Path(location["root"])
    if location["scheme"] in _local_schemes:
        if alt_base is not None:
            root = Path(alt_base) / root.name
        return resource_path(root, location["resource_name"])
    else:
        # the root contains the netloc and the base path
        netloc = root.parts[0]
        # this will strip off any trailing slash
        path = Path(*root.parts[1:], location["resource_name"])
        return urlunparse(
            (
                location["scheme"],
                netloc,
                f"{path}/",
                "",
                "",
                "",
            )
        )


def query_registry(
    session: Any,
    url: str,
    params: Optional[Mapping[str, Any]] = None,
    auth: Optional[str] = None,
) -> Optional[Dict]:
    """Perform a GET request to url with params. Returns None for 404 HTTP errors"""
    r = session.get(
        url,
        params=params,
        headers={"Accept": "application/json"},
        auth=auth,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def query_registry_paginated(
    session: Any, url: str, params: Optional[Mapping[str, Any]] = None
) -> Iterator[Dict]:
    """Perform GET request(s) to yield records from a paginated endpoint"""
    r = session.get(url, params=params, headers={"Accept": "application/json"})
    r.raise_for_status()
    for d in r.json():
        yield d
    while "next" in r.links:
        url = r.links["next"]["url"]
        # parameters are already part of the URL
        r = session.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        for d in r.json():
            yield d


def query_registry_first(
    session: Any, url: str, params: Optional[Mapping[str, Any]] = None
) -> Dict:
    """Perform a GET response to a url and return the first result or None"""
    try:
        return next(query_registry_paginated(session, url, params))
    except StopIteration:
        return None


def download_to_file(session: Any, url: str, target: Union[Path, str]) -> None:
    """Download contents of url to target"""
    with session.stream("GET", url) as r:
        r.raise_for_status()
        with open(target, "wb") as fp:
            for chunk in r.iter_bytes(chunk_size=1024):
                fp.write(chunk)


def query_registry_bulk(
    session: Any, url: str, query: Mapping[str, Any], auth: Optional[str] = None
) -> List[Dict]:
    """Perform a POST request to a bulk query url. These endpoints all stream line-delimited json"""
    with session.stream("POST", url, json=query, auth=auth) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            yield json.loads(line)


__all__ = [
    "parse_location",
    "query_registry",
    "query_registry_bulk",
    "query_registry_paginated",
    "download_to_file",
]
