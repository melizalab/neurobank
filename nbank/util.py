# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""utility functions

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Tue Jul  8 14:23:35 2014
"""
from pathlib import Path
from typing import Union, Dict, Any, Iterator


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


def query_registry(
    session: Any, url: str, params: Union[Dict, None] = None
) -> Union[Dict, None]:
    """Perform a GET request to url with params."""
    r = session.get(
        url, params=params, headers={"Accept": "application/json"}, verify=True
    )
    if r.status_code == 404:
        raise ValueError(f"{url} not found")
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
        if r.status_code == 404:
            raise ValueError(f"The resource {id} does not exist or is not downloadable")
        r.raise_for_status()
        with open(target, "wb") as fp:
            for chunk in r.iter_content(chunk_size=1024):
                fp.write(chunk)
