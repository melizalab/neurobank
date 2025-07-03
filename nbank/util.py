# -*- mode: python -*-
"""utility functions

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Tue Jul  8 14:23:35 2014
"""

import json
import logging
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from httpx import Client

from nbank import archive, tape_archive
from nbank.types import FetchableResource, NotFetchableError, Resource

log = logging.getLogger("nbank")  # root logger


class HttpResource(FetchableResource):
    """A resource that can be fetched from an HTTP(S) endpoint"""

    schemes = ("http", "https")

    def __init__(self, location: Mapping[str, str], session: Optional[Client] = None):
        from urllib.parse import urlunparse

        assert location["scheme"] in (
            "http",
            "https",
        ), "location scheme is not 'http' or 'https'"
        self.session = session
        root = Path(location["root"])
        # the root contains the netloc and the base path
        netloc = root.parts[0]
        # this will strip off any trailing slash
        path = Path(*root.parts[1:], location["resource_name"])
        self.url = urlunparse(
            (
                location["scheme"],
                netloc,
                f"{path}/",
                "",
                "",
                "",
            )
        )

    def __str__(self):
        return self.url

    def fetch(self, target: Path) -> Path:
        if self.session is None:
            raise NotFetchableError(
                "No mechanism provided to fetch a resource over http(s)"
            )
        with self.session.stream("GET", self.url) as r:
            r.raise_for_status()
            with open(target, "wb") as fp:
                for chunk in r.iter_bytes(chunk_size=1024):
                    fp.write(chunk)
        return target


def parse_location(
    location: Mapping[str, str],
    *,
    alt_base: Optional[Path] = None,
    http_session: Optional[Client] = None,
) -> Optional[Resource]:
    """Parse a location dict and return a Resource or None if the location is invalid.

    location is a dict with 'scheme', 'root', and 'resource_name'.

    """
    scheme = location["scheme"]
    # TODO: replace hard-coded dispatch - plugin?
    if scheme == "neurobank":
        try:
            return archive.Resource(
                location["root"], location["resource_name"], alt_base
            )
        except FileNotFoundError:
            pass
    elif scheme in ("http", "https"):
        return HttpResource(location, http_session)
    elif scheme == "tape":
        return tape_archive.Resource(
            location["root"], location["resource_name"], alt_base
        )
    else:
        log.debug("Unrecognized location scheme %s", scheme)


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


def hash(fname: Path, method: str = "sha1") -> str:
    """Returns a hash of the contents of fname using method.

    fname can be the path to a regular file or a directory.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    import hashlib

    p = fname.resolve(strict=True)
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


def hash_directory(path: Path, method: str = "sha1") -> str:
    """Return hash of the contents of the directory at path using method.

    Any secure hash method supported by python's hashlib
    library is supported. Raises errors for invalid files or methods.

    """
    import hashlib

    p = path.resolve(strict=True)
    hashes = []
    for fn in sorted(p.rglob("*")):
        if not fn.is_file():
            continue
        fn_rel = fn.relative_to(p)
        with open(fn, "rb") as fp:
            hashes.append(f"{fn_rel}={hashlib.new(method, fp.read()).hexdigest()}")
    # log.debug("directory hashes of %s: %s", path, hashes)
    return hashlib.new(method, "\n".join(hashes).encode("utf-8")).hexdigest()


def query_registry(
    session: Client,
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
    session: Client, url: str, params: Optional[Mapping[str, Any]] = None
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
    session: Client, url: str, params: Optional[Mapping[str, Any]] = None
) -> Dict:
    """Perform a GET response to a url and return the first result or None"""
    try:
        return next(query_registry_paginated(session, url, params))
    except StopIteration:
        return None


def query_registry_bulk(
    session: Client, url: str, query: Mapping[str, Any], auth: Optional[str] = None
) -> List[Dict]:
    """Perform a POST request to a bulk query url. These endpoints all stream line-delimited json"""
    with session.stream("POST", url, json=query, auth=auth) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            yield json.loads(line)


def fetch_resource(
    session: Client,
    locations: Sequence[dict],
    target: Path,
    *,
    force: bool = False,
    extension: Optional[str] = None,
    alt_base: Optional[Path] = None,
) -> Union[Path, NotFetchableError, FileExistsError]:
    """Fetch a resource from an archive.

    Tries local resources, then remote ones. Local resources are copied to the
    target directory; remote resources are downloaded. Stops after the first
    success.

    Returns the path of the downloaded file if successful, NotFetchableError if
    the resource could not be fetched, or FileExistsError if the target already
    exists.

    """
    if target.is_dir():
        raise FileNotFoundError("target file must be a filename, not a directory")
    if extension:
        target = target.with_suffix(f".{extension}")
    if target.exists():
        if force:
            log.debug("removing target file %s", target)
            target.unlink()
        else:
            return FileExistsError(f"(target file {target} already exists)")
    loc_parsed = (
        parse_location(loc, alt_base=alt_base, http_session=session)
        for loc in locations
    )
    sorted_locations = sorted(
        (loc for loc in loc_parsed if loc is not None),
        key=lambda x: not hasattr(x, "path"),
    )

    for location in sorted_locations:
        log.debug("trying %s", location)
        try:
            return location.fetch(target)
        except NotFetchableError:
            continue
    return NotFetchableError("(no valid locations)")


__all__ = [
    "parse_location",
    "query_registry",
    "query_registry_bulk",
    "query_registry_paginated",
]
