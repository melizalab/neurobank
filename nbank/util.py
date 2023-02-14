# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""utility functions

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Tue Jul  8 14:23:35 2014
"""
from pathlib import Path
from typing import Union, Dict, Any, Iterator, Optional


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
            hashes.append(
                "{}={}".format(
                    fn,
                    hashlib.new(method, fp.read()).hexdigest(),
                )
            )
    return hashlib.new(method, "\n".join(hashes).encode("utf-8")).hexdigest()


def parse_location(
    location: Dict, alt_base: Union[Path, str, None] = None
) -> Union[Path, str]:
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
    from nbank.archive import resource_path
    from urllib.parse import urlunparse

    if location["scheme"] == "neurobank":
        root = Path(location["root"])
        if alt_base is not None:
            root = Path(alt_base) / root.name
        return resource_path(root, location["resource_name"])
    else:
        return urlunparse(
            (
                location["scheme"],
                location["root"],
                location["resource_name"],
                "",
                "",
                "",
            )
        )


def query_registry(
    session: Any, url: str, params: Optional[Dict] = None, auth: Optional[str] = None
) -> Optional[Dict]:
    """Perform a GET request to url with params. Returns None for 404 HTTP errors"""
    r = session.get(
        url,
        params=params,
        headers={"Accept": "application/json"},
        auth=auth,
        verify=True,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def query_registry_paginated(session: Any, url: str, params: Dict) -> Iterator[Dict]:
    """Perform GET request(s) to yield records from a paginated endpoint"""
    r = session.get(
        url, params=params, headers={"Accept": "application/json"}, verify=True
    )
    r.raise_for_status()
    for d in r.json():
        yield d
    while "next" in r.links:
        url = r.links["next"]["url"]
        # we may need to throttle request rate
        r = session.get(
            url, params=params, headers={"Accept": "application/json"}, verify=True
        )
        r.raise_for_status()
        for d in r.json():
            yield d


def download_to_file(session: Any, url: str, target: Union[Path, str]) -> None:
    """Download contents of url to target"""
    with session.get(url, stream=True) as r:
        # if r.status_code == 404:
        #     raise ValueError(f"The resource {id} does not exist or is not downloadable")
        r.raise_for_status()
        with open(target, "wb") as fp:
            for chunk in r.iter_content(chunk_size=1024):
                fp.write(chunk)
