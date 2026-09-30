# -*- mode: python -*-
"""utility functions

Copyright (C) 2014 Dan Meliza <dan@meliza.org>
Created Tue Jul  8 14:23:35 2014
"""

import json
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, BinaryIO

from httpx import Client

# imported so that their resource classes register their location schemes
from nbank import archive, tape_archive  # noqa: F401
from nbank.types import (
    FetchableResource,
    NotFetchableError,
    Resource,
    location_class,
    location_scheme,
)

log = logging.getLogger("nbank")  # root logger

# names per request to the registry's bulk endpoints
bulk_batch_size = 500


@location_scheme
class HttpResource(FetchableResource):
    """A resource that can be fetched from an HTTP(S) endpoint"""

    schemes = ("http", "https")

    @classmethod
    def from_location(cls, location, *, alt_base=None, http_session=None):
        return cls(location, http_session)

    def __init__(self, location: Mapping[str, str], session: Client | None = None):
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
        self.id = location["resource_name"]
        path = Path(*root.parts[1:], self.id)
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

    def __repr__(self):
        return f"<remote resource: {self.id} @ {self.path}>"

    def fetch(self, target: Path) -> Path:
        if self.session is None:
            raise NotFetchableError(
                "No mechanism provided to fetch a resource over http(s)"
            )
        with self.session.stream("GET", self.url) as r:
            if r.is_error:
                r.read()  # the body can't be read once the stream is closed
            r.raise_for_status()
            with open(target, "wb") as fp:
                for chunk in r.iter_bytes(chunk_size=1024):
                    fp.write(chunk)
        return target


def parse_location(
    location: Mapping[str, str],
    *,
    alt_base: Path | None = None,
    http_session: Client | None = None,
) -> Resource | None:
    """Parse a location dict and return a Resource or None if the location is invalid.

    location is a dict with 'scheme', 'root', and 'resource_name', and 'key' if
    the registry records where the resource is within its archive. Returns None
    for schemes the client doesn't know and for local resources that don't
    exist.

    """
    scheme = location["scheme"]
    cls = location_class(scheme)
    if cls is None:
        log.debug("Unrecognized location scheme %s", scheme)
        return None
    return cls.from_location(location, alt_base=alt_base, http_session=http_session)


def id_from_fname(fname: Path | str) -> str:
    """Generates an ID from the basename of fname, stripped of any extensions.

    Raises ValueError unless the resulting id only contains URL-unreserved characters
    ([-_~0-9a-zA-Z]). This is a fast local sanity check, not a guarantee that the
    registry will accept the id: the registry has its own, narrower rules (e.g., it
    doesn't allow '~'), and is the final authority on what ids are valid.
    """
    import re

    id = Path(fname).stem
    if re.match(r"^[-_~0-9a-zA-Z]+$", id) is None:
        raise ValueError(f"resource name '{id}' contains invalid characters")
    return id


_hash_block_size = 1 << 20


def hash_stream(
    source: BinaryIO,
    method: str = "sha1",
    copy_to: BinaryIO | None = None,
    progress: Callable[[int], None] | None = None,
) -> str:
    """Returns the hash of everything read from source, using method.

    Reads in fixed-size blocks, so memory use doesn't depend on the size of the
    data. If copy_to is given, each block is also written to it, so data can be
    copied and hashed in one pass. If progress is given, it's called after each
    block with the number of bytes read so far.
    """
    import hashlib

    digest = hashlib.new(method)
    nbytes = 0
    while True:
        data = source.read(_hash_block_size)
        if not data:
            break
        digest.update(data)
        if copy_to is not None:
            copy_to.write(data)
        nbytes += len(data)
        if progress is not None:
            progress(nbytes)
    return digest.hexdigest()


class DirectoryHasher:
    """Computes the hash of a directory resource from the hashes of its files.

    Files can be added in any order, so the hash can be computed from a
    directory on disk or from a stream such as a tar file. Each file is
    identified by its path relative to the directory, with '/' separators. The
    result is the hash of the lines `<path>=<file hash>`, sorted by path
    component and joined with newlines. Directories themselves (including empty
    ones) don't contribute to the hash.
    """

    def __init__(self, method: str = "sha1"):
        self.method = method
        self._files: dict[PurePosixPath, str] = {}

    def add(self, relpath: str | PurePath, file_hash: str) -> None:
        """Records the hash of the file at relpath. Raises ValueError on a repeat."""
        key = PurePosixPath(relpath)
        if key in self._files:
            raise ValueError(f"'{key}' was added more than once")
        self._files[key] = file_hash

    def add_stream(
        self,
        relpath: str | PurePath,
        source: BinaryIO,
        progress: Callable[[int], None] | None = None,
    ) -> str:
        """Hashes a file read from source and records it. Returns the file's hash.

        progress is passed to hash_stream.
        """
        file_hash = hash_stream(source, self.method, progress=progress)
        self.add(relpath, file_hash)
        return file_hash

    def hexdigest(self) -> str:
        import hashlib

        lines = [
            f"{path}={self._files[path]}"
            for path in sorted(self._files, key=lambda p: p.parts)
        ]
        return hashlib.new(self.method, "\n".join(lines).encode("utf-8")).hexdigest()


def hash(fname: Path, method: str = "sha1") -> str:
    """Returns a hash of the contents of fname using method.

    fname can be the path to a regular file or a directory.

    Any secure hash method supported by python's hashlib library is supported.
    Raises errors for invalid files or methods.

    """
    p = fname.resolve(strict=True)
    if p.is_dir():
        return hash_directory(p, method)
    with open(p, "rb") as fp:
        return hash_stream(fp, method)


def hash_directory(path: Path, method: str = "sha1") -> str:
    """Return hash of the contents of the directory at path using method.

    See DirectoryHasher for how the hash is computed. Any secure hash method
    supported by python's hashlib library is supported. Raises errors for
    invalid files or methods.

    """
    p = path.resolve(strict=True)
    hasher = DirectoryHasher(method)
    for fn in p.rglob("*"):
        if not fn.is_file():
            continue
        with open(fn, "rb") as fp:
            hasher.add_stream(fn.relative_to(p).as_posix(), fp)
    return hasher.hexdigest()


def query_registry(
    session: Client,
    url: str,
    params: Mapping[str, Any] | None = None,
    auth: str | None = None,
) -> dict | None:
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
    session: Client, url: str, params: Mapping[str, Any] | None = None
) -> Iterator[dict]:
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
    session: Client, url: str, params: Mapping[str, Any] | None = None
) -> dict:
    """Perform a GET response to a url and return the first result or None"""
    try:
        return next(query_registry_paginated(session, url, params))
    except StopIteration:
        return None


def query_registry_bulk(
    session: Client, url: str, query: Mapping[str, Any], auth: str | None = None
) -> list[dict]:
    """Perform a POST request to a bulk query url. These endpoints all stream line-delimited json"""
    with session.stream("POST", url, json=query, auth=auth) as r:
        if r.is_error:
            r.read()  # the body can't be read once the stream is closed
        r.raise_for_status()
        for line in r.iter_lines():
            yield json.loads(line)


def fetch_resource(
    session: Client,
    locations: Sequence[dict],
    target: Path,
    *,
    force: bool = False,
    extension: str | None = None,
    alt_base: Path | None = None,
) -> Path | NotFetchableError | FileExistsError:
    """Fetch a resource from an archive.

    Relies on the registry returning local locations before remote ones. Stops
    after the first success.

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
    for loc in locations:
        location = parse_location(loc, alt_base=alt_base, http_session=session)
        log.debug("trying %s", location)
        try:
            return location.fetch(target)
        except (AttributeError, NotFetchableError):
            continue
    return NotFetchableError("(no valid locations)")


class JSONEncoder(json.JSONEncoder):
    """JSON encoder that serializes pathlib objects as strings."""

    def default(self, o):
        if isinstance(o, PurePath):
            return str(o)
        return super().default(o)


__all__ = [
    "parse_location",
    "query_registry",
    "query_registry_bulk",
    "query_registry_paginated",
]
