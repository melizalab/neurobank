# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Utility functions"""

import json
import logging
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, ClassVar

from httpx import (
    URL,
    USE_CLIENT_DEFAULT,
    Auth,
    BasicAuth,
    Client,
    NetRCAuth,
    Response,
    codes,
)

# imported so that their resource classes register their location schemes
from nbank import archive, registry, tape_archive  # noqa: F401
from nbank.types import (
    FetchableResource,
    LocationContext,
    NonFetchableResource,
    NotFetchableError,
    Readable,
    Resource,
    Writable,
    location_class,
    location_scheme,
)

log = logging.getLogger("nbank")  # root logger

# names per request to the registry's bulk endpoints
bulk_batch_size = 500
# resources per request to the registry's check endpoint (its limit is 1000)
check_batch_size = 1000

# the registry API version this client asks for in every request
API_VERSION = "2.0"
# the media type of line-delimited JSON, for streamed searches
NDJSON = "application/x-ndjson"
# registries whose API version has been checked in this process
_checked_registries: set[str] = set()


class RegistryVersionError(RuntimeError):
    """The registry doesn't support the API version this client needs."""


class _RegistryAuth(Auth):
    """Authenticates requests to the registry's host and port, and no others, so the
    registry's credentials aren't sent to hosts that its locations name."""

    def __init__(self, auth, registry_url: str):
        self.auth = BasicAuth(*auth) if isinstance(auth, tuple) else auth
        self.origin = _origin(URL(registry_url))

    def sync_auth_flow(self, request):
        if _origin(request.url) != self.origin:
            yield request
            return
        yield from self.auth.sync_auth_flow(request)


def _origin(url: URL) -> tuple[str, str, int | None]:
    default = {"http": 80, "https": 443}.get(url.scheme)
    return (url.scheme, url.host, url.port or default)


@contextmanager
def registry_session(
    registry_url: str | None = None, *, auth=None, check_version: bool = True
) -> Generator[Client, None, None]:
    """Yields an HTTP client that asks for API_VERSION in every request.

    auth (an httpx Auth or a (user, password) tuple) is used only for requests to
    registry_url's host, and needs registry_url. A NetRCAuth is used for every
    host, as it holds each host's own credentials. If registry_url is given and
    check_version is true, first checks that the registry supports API_VERSION (see
    check_registry_version).
    """
    if auth is not None and not isinstance(auth, NetRCAuth):
        if registry_url is None:
            raise ValueError("auth needs the registry_url it's for")
        auth = _RegistryAuth(auth, registry_url)
    headers = {"Accept": f"application/json; version={API_VERSION}"}
    with Client(auth=auth, headers=headers) as session:
        if registry_url is not None and check_version:
            check_registry_version(session, registry_url)
        yield session


def check_registry_version(session: Client, registry_url: str) -> None:
    """Raises RegistryVersionError if the registry at registry_url doesn't
    report an API with API_VERSION's major version. Each registry is checked once
    per process.
    """
    url, params = registry.get_info(registry_url)
    if url in _checked_registries:
        return
    info = query_registry(session, url, params)
    version = (info or {}).get("api_version")
    if version is None or version.split(".")[0] != API_VERSION.split(".")[0]:
        major = API_VERSION.split(".")[0]
        raise RegistryVersionError(
            f"the registry at {registry_url} has API version {version or 'unknown'}; "
            f"this version of neurobank needs API {major}.x"
        )
    _checked_registries.add(url)


@location_scheme
class HttpResource(FetchableResource):
    """A resource that can be fetched from an HTTP(S) endpoint"""

    schemes: ClassVar[tuple[str, ...]] = ("http", "https")

    @classmethod
    def from_location(cls, location, context):
        return cls(location, context.http_session)

    def __init__(self, location: Mapping[str, str], session: Client | None = None):
        from urllib.parse import urlunparse

        assert location["scheme"] in (
            "http",
            "https",
        ), "location scheme is not 'http' or 'https'"
        self.session = session
        # the root contains the netloc and the base path; a URL path has forward
        # slashes on any host
        root = PurePosixPath(location["root"])
        netloc = root.parts[0]
        self.host = netloc
        # this will strip off any trailing slash
        self.id = location["resource_name"]
        path = PurePosixPath(*root.parts[1:], self.id)
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
        return f"<remote resource: {self.id} @ {self.url}>"

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
    """Returns a resource for a registry location dict, or None.

    The dict has 'scheme', 'root', 'resource_name', and optionally 'key'. None means the
    scheme isn't known or the resource isn't on this host. alt_base, if given, replaces
    the directory that contains each archive, for copies of archives on another host
    (see archive.Resource). http_session is used to fetch http(s) resources.
    """
    scheme = location["scheme"]
    cls = location_class(scheme)
    if cls is None:
        log.debug("Unrecognized location scheme %s", scheme)
        return None
    context = LocationContext(alt_base=alt_base, http_session=http_session)
    return cls.from_location(location, context)


def id_from_fname(fname: Path | str) -> str:
    """Returns the base name of fname without extensions, as a resource id.

    Raises ValueError unless the id contains only [-_~0-9a-zA-Z]. This is a quick sanity
    check; the registry decides which ids are valid.
    """
    import re

    id = Path(fname).stem
    if re.match(r"^[-_~0-9a-zA-Z]+$", id) is None:
        raise ValueError(f"resource name '{id}' contains invalid characters")
    return id


_id_alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"


def base36_id(length: int = 8) -> str:
    """Returns a random id of lowercase base36 characters, the form the registry gives
    a resource registered without a name."""
    import secrets

    return "".join(secrets.choice(_id_alphabet) for _ in range(length))


def uuid_id() -> str:
    """Returns a random UUID (version 4) as an id."""
    import uuid

    return str(uuid.uuid4())


# the archive policy's auto_id_type names one of these; null means base36
auto_id_types: dict[str, Callable[[], str]] = {"base36": base36_id, "uuid": uuid_id}


_hash_block_size = 1 << 20


def hash_stream(
    source: Readable,
    method: str = "sha1",
    copy_to: Writable | None = None,
    progress: Callable[[int], None] | None = None,
) -> str:
    """Returns the hash of everything read from source.

    method is the name of a hashlib algorithm. Each block read is also written to
    copy_to, if given, and progress is called with the number of bytes read so far.
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


def hash(fname: Path, method: str = "sha1") -> str:
    """Returns the hash of the file fname.

    method is any algorithm hashlib supports.
    """
    with open(fname, "rb") as fp:
        return hash_stream(fp, method)


def query_registry(
    session: Client,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    auth=USE_CLIENT_DEFAULT,
) -> dict | None:
    """Perform a GET request to url with params. Returns None for 404 HTTP errors.
    The request uses the session's auth unless auth is given."""
    r = session.get(url, params=params, auth=auth)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def query_registry_paginated(
    session: Client, url: str, params: Mapping[str, Any] | None = None
) -> Iterator[dict]:
    """Perform GET request(s) to yield records from a paginated endpoint"""
    yield from _follow_pages(session, session.get(url, params=params))


def _follow_pages(session: Client, r: Response) -> Iterator[dict]:
    """Yields the records in r, a page of results, and in the pages after it."""
    while True:
        r.raise_for_status()
        yield from r.json()
        if "next" not in r.links:
            return
        # parameters are already part of the URL
        r = session.get(r.links["next"]["url"])


def query_registry_stream(
    session: Client, url: str, params: Mapping[str, Any] | None = None
) -> Iterator[dict]:
    """Yields every record from a search of the registry, streamed as line-delimited
    JSON in one response. A registry that can't stream them (406) is paged through
    instead. For results that are read to the end: the registry produces all of
    them, however few are read."""
    headers = {"Accept": f"{NDJSON}; version={API_VERSION}"}
    with session.stream("GET", url, params=params, headers=headers) as r:
        if r.headers.get("content-type", "").startswith(NDJSON):
            if r.is_error:
                r.read()  # the body can't be read once the stream is closed
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    yield json.loads(line)
            return
        if r.status_code != codes.NOT_ACCEPTABLE:
            r.read()
            yield from _follow_pages(session, r)
            return
    yield from query_registry_paginated(session, url, params)


def query_registry_first(
    session: Client, url: str, params: Mapping[str, Any] | None = None
) -> dict | None:
    """Perform a GET response to a url and return the first result or None"""
    try:
        return next(query_registry_paginated(session, url, params))
    except StopIteration:
        return None


def query_registry_bulk(
    session: Client, url: str, query: Mapping[str, Any], *, auth=USE_CLIENT_DEFAULT
) -> Iterator[dict]:
    """POSTs query to a bulk endpoint and yields the line-delimited JSON records.
    The request uses the session's auth unless auth is given."""
    with session.stream("POST", url, json=query, auth=auth) as r:
        if r.is_error:
            r.read()  # the body can't be read once the stream is closed
        r.raise_for_status()
        for line in r.iter_lines():
            yield json.loads(line)


def query_registry_bulk_batched(
    session: Client,
    request: Callable[[list[str]], tuple[str, Mapping[str, Any]]],
    names: Iterable[str],
) -> Iterator[dict]:
    """Yields the records from a bulk endpoint for names, asking for bulk_batch_size of
    them per request. request(batch) returns the url and query for a batch of names, as
    registry.get_resource_bulk does."""
    names = list(names)
    for i in range(0, len(names), bulk_batch_size):
        url, query = request(names[i : i + bulk_batch_size])
        yield from query_registry_bulk(session, url, query)


def fetch_resource(
    session: Client,
    locations: Sequence[dict],
    target: Path,
    *,
    force: bool = False,
    extension: str | None = None,
    alt_base: Path | None = None,
    sha1: str | None = None,
) -> Path | NotFetchableError | FileExistsError:
    """Copies or downloads a resource to target from the first location that works.

    locations are registry location dicts, tried in order. extension, if given, replaces
    the suffix of target. alt_base, if given, replaces the directory that contains each
    archive, for copies of archives on another host (see archive.Resource). If sha1 is
    given, a copy that doesn't match it is removed and the next location tried. Returns
    the path written, or (not raises) NotFetchableError if no location works and
    FileExistsError if target exists and force isn't set. Raises FileNotFoundError if
    target is a directory.
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
    mismatched = False
    for loc in locations:
        location = parse_location(loc, alt_base=alt_base, http_session=session)
        log.debug("trying %s", location)
        if location is None or isinstance(location, NonFetchableResource):
            continue
        try:
            path = location.fetch(target)
        except NotFetchableError:
            continue
        if sha1 is None or hash(path) == sha1:
            return path
        log.debug("%s doesn't match its registered hash", location)
        path.unlink()
        mismatched = True
    if mismatched:
        return NotFetchableError("(no copy matches the registered hash)")
    return NotFetchableError("(no valid locations)")


class JSONEncoder(json.JSONEncoder):
    """JSON encoder that serializes pathlib objects as strings."""

    def default(self, o):
        if isinstance(o, PurePath):
            return str(o)
        return super().default(o)


__all__ = [
    "RegistryVersionError",
    "parse_location",
    "query_registry",
    "query_registry_bulk",
    "query_registry_paginated",
    "registry_session",
]
