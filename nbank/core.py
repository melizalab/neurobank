# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Core functions for managing data registry and archives: depositing resources,
searching the registry, locating and/or retrieving resources, verifying their integrity,
and updating metadata.

"""

import logging
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx

# types that can be turned into authentication for httpx
RegistryAuth = tuple[str, str] | httpx.Auth | None

log = logging.getLogger("nbank")  # root logger

# how many automatic ids deposit tries for a file before giving up, if the registry
# already has them
_auto_id_attempts = 3


def make_auth(auth: RegistryAuth) -> httpx.Auth | None:
    """Converts a RegistryAuth to an httpx Auth, using a netrc file if auth is None.

    The netrc file is ~/.netrc, or ~/_netrc (the Windows name) if there's no
    ~/.netrc. None if there's neither.
    """
    if isinstance(auth, httpx.Auth):
        return auth
    if isinstance(auth, tuple):
        return httpx.BasicAuth(*auth)
    try:
        # the default file, so that netrc checks its permissions
        return httpx.NetRCAuth()
    except FileNotFoundError:
        pass
    try:
        return httpx.NetRCAuth(str(Path.home() / "_netrc"))
    except FileNotFoundError:
        pass


def deposit(
    archive_path: Path,
    files: Iterable[Path],
    *,
    dtype: str | None = None,
    hash: bool = False,
    auto_id: bool = False,
    auth: RegistryAuth = None,
    dry_run: bool = False,
    skip_errors: bool = False,
    read_metadata: bool = True,
    override_file_metadata: bool = False,
    zip_directories: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[dict]:
    """Registers files and moves them into the archive at archive_path.

    dtype is the registered datatype of the files. hash registers each file's sha1 even
    if the archive doesn't require it. auto_id (or the archive's auto_identifiers
    policy) gives each file a new id instead of using the file's name: 8 random base36
    characters, or a UUID if the policy's auto_id_type is "uuid". If the registry
    already has the id, another is tried. Each location is registered with the stored
    file's name as its key. auth is for
    the registry (default: .netrc). metadata is stored with each resource; its
    experimenter and animal keys are the resource's fields.

    If a package provides a metadata reader for dtype (see nbank.readers), each file is
    checked and its metadata read with it, unless read_metadata is False. The file's
    metadata is merged with the metadata given here; a key with different values in
    the two is an error for that file, unless override_file_metadata, in which case
    the given value is used.

    Yields {"source": path, "id": id} for each file deposited. With dry_run, checks
    everything but registers and moves nothing, including asking the registry whether
    it would accept each resource (in batches, so records come after their files are
    checked, and files that would share a name or hash are caught); records have
    "dry_run": True and the "metadata" that would be registered. With skip_errors, a
    file that can't be deposited yields {"source": path, "error": message} instead of
    raising. That covers missing files, directories,
    invalid names, symbolic links, permission problems, files their dtype's reader
    rejects or whose metadata doesn't match what's given, and resources the registry
    rejects (400).

    Directories can't be deposited as they are. With zip_directories, each one is
    written to a zip in canonical form (see nbank.canonical_zip) and the zip is
    deposited, named for the directory; the directory itself is left in place, and
    the record has "zipped": True. Dry runs don't build the zip, so they don't check
    it with the dtype's reader or hash it. Without zip_directories, and without
    skip_errors, missing files and directories are skipped with a log message.
    These errors are raised:

    - ValueError: archive_path isn't an archive, its auto_id_type isn't known, a
      source is or has a symbolic link, or the dtype's reader rejects a file or its
      metadata doesn't match
    - RuntimeError: the archive or dtype isn't registered, the dtype's reader can't be
      loaded, or a file changed while its directory was being zipped
    - PermissionError: a source can't be read or the archive can't be written
    - FileExistsError: a file is already stored for the id (nothing is registered)
    - httpx.HTTPStatusError: the registry refuses a resource
    - ValueError: in a dry run, the registry would refuse a resource
    - httpx.ConnectError: the registry can't be reached
    - KeyError: a file for an id appeared after the check, as the archive and registry
      disagree
    - archive.ArchiveHostError: this is a Windows host, where archives aren't written
    """
    from nbank import canonical_zip, readers, util
    from nbank.archive import (
        ArchiveConfig,
        new_resource_path,
        require_posix,
        staging_dir,
        store_resource,
        verify_no_symlinks,
        verify_permissions,
    )
    from nbank.registry import (
        add_resource,
        check_resources,
        error_messages,
        field_messages,
        find_archive_by_path,
        full_url,
        get_datatypes,
    )

    require_posix("deposit")
    try:
        archive_cfg = ArchiveConfig.load(archive_path)
    except FileNotFoundError as err:
        raise ValueError(f"{archive_path} is not a valid archive") from err
    archive_path = archive_cfg.path
    if dry_run:
        log.info("DRY RUN: no changes will be made")
    log.info("archive: %s", archive_path)
    registry_url = archive_cfg.registry
    log.info("   registry: %s", registry_url)
    auto_id = archive_cfg.policy.auto_identifiers or auto_id
    auto_id_type = archive_cfg.policy.auto_id_type
    if auto_id and auto_id_type not in util.auto_id_types:
        raise ValueError(f"the archive's auto_id_type '{auto_id_type}' isn't known")

    with util.registry_session(registry_url, auth=make_auth(auth)) as session:
        # check that archive exists for this path
        url, params = find_archive_by_path(registry_url, archive_path)
        archive_record = util.query_registry_first(session, url, params)
        if archive_record is None:
            raise RuntimeError(
                f"archive '{archive_path}' not in registry. did it move?"
            )
        archive = archive_record["name"]
        log.info("   archive name: %s", archive)

        # check the dtype before hashing any files
        if dtype is not None:
            url, params = get_datatypes(registry_url)
            known_dtypes = {
                d["name"] for d in util.query_registry_paginated(session, url, params)
            }
            if dtype not in known_dtypes:
                raise RuntimeError(f"'{dtype}' is not a registered datatype")
        else:
            log.warning(
                "   warning: no dtype given, so file metadata won't be read "
                "(and the registry may refuse the deposit)"
            )

        given = dict(metadata or {})
        staging = staging_dir(archive_cfg)
        reader = None
        if dtype is not None and read_metadata:
            reader = readers.find_reader(dtype)
            if reader is None:
                log.info("   no metadata reader for %s", dtype)

        def skipped(src: Path, message: str) -> dict:
            log.error("   error: %s", message)
            return {"source": src, "error": message}

        # dry runs check resources with the registry in batches, which also
        # catches files that would have the same name or hash as each other
        pending = []

        def check_pending() -> Iterator[dict]:
            url, bodies = check_resources(
                registry_url, [body for _, body, _ in pending]
            )
            r = session.post(url, json=bodies)
            r.raise_for_status()
            for (src, _, record), errors in zip(pending, r.json(), strict=True):
                if not errors:
                    log.info("%s: OK (dry run; nothing registered or moved)", src)
                    yield record
                    continue
                message = "; ".join(field_messages(errors))
                if not skip_errors:
                    raise ValueError(f"{src}: the registry would refuse it: {message}")
                log.error("%s: error: %s", src, message)
                yield {"source": src, "error": message}
            pending.clear()

        for src in files:
            log.info("processing '%s':", src)
            if not src.exists():
                if skip_errors:
                    yield skipped(src, "does not exist")
                else:
                    log.info("   does not exist; skipping")
                continue
            is_dir = src.is_dir()
            if is_dir and not zip_directories:
                message = (
                    "is a directory; zip it first, or deposit with --zip-directories"
                )
                if skip_errors:
                    yield skipped(src, message)
                else:
                    log.info("   %s; skipping", message)
                continue
            zipped = {"zipped": True} if is_dir else {}
            # the zip is built in the archive's staging directory, so storing it is a
            # rename; nbank check reports it if a deposit is interrupted
            staged = None
            try:
                try:
                    if auto_id:
                        id = util.auto_id_types[auto_id_type]()
                    else:
                        id = util.id_from_fname(src)
                    stored_name = f"{src.name}.zip" if is_dir else src.name
                    key = new_resource_path(archive_cfg, id, stored_name).name
                    verify_no_symlinks(src)
                    verify_permissions(archive_cfg, src, id)
                    checkable = not (is_dir and dry_run)
                    if is_dir and not staging.is_dir():
                        raise FileNotFoundError(
                            f"the archive has no staging directory ({staging}); "
                            "run 'nbank check archive --fix' to create it"
                        )
                    if is_dir and not dry_run:
                        staged = canonical_zip.zip_directory(src, staging / stored_name)
                        log.info("   zipped as %s", staged)
                    elif is_dir:
                        log.info("   would be zipped (not built in a dry run)")
                    source = staged or src
                    if reader is not None and checkable:
                        assert dtype is not None  # reader is for dtype
                        file_metadata = readers.read_file_metadata(
                            reader, dtype, source
                        )
                        log.debug("   from the file: %s", file_metadata)
                        resource_metadata = readers.merge_metadata(
                            file_metadata, given, override=override_file_metadata
                        )
                        log.info("   metadata: %s", resource_metadata)
                    else:
                        resource_metadata = given
                    if (hash or archive_cfg.policy.require_hash) and checkable:
                        sha1 = util.hash(source)
                        log.info("   sha1: %s", sha1)
                    else:
                        sha1 = None
                except (ValueError, OSError, RuntimeError) as err:
                    if not skip_errors:
                        raise
                    yield skipped(src, str(err))
                    continue
                if dry_run:
                    _, body = add_resource(
                        registry_url, id, dtype, archive, sha1, key, **resource_metadata
                    )
                    record = {
                        "source": src,
                        "id": id,
                        "dry_run": True,
                        "metadata": resource_metadata,
                        **zipped,
                    }
                    pending.append((src, body, record))
                    if len(pending) == util.check_batch_size:
                        yield from check_pending()
                    continue
                attempts = 1
                while True:
                    url, params = add_resource(
                        registry_url, id, dtype, archive, sha1, key, **resource_metadata
                    )
                    log.debug("POST %s: %s", url, params)
                    r = session.post(url, json=params)
                    if not (
                        auto_id and attempts < _auto_id_attempts and _name_taken(r)
                    ):
                        break
                    log.info("   id '%s' is taken; trying another", id)
                    id = util.auto_id_types[auto_id_type]()
                    key = new_resource_path(archive_cfg, id, stored_name).name
                    attempts += 1
                if skip_errors and r.status_code == httpx.codes.BAD_REQUEST:
                    yield skipped(src, "; ".join(error_messages(r)))
                    continue
                r.raise_for_status()
                result = r.json()

                log.info("   registered as %s", full_url(registry_url, result["name"]))
                tgt = store_resource(archive_cfg, source, id=result["name"])
                log.info("   deposited in %s", tgt)
                yield {"source": src, "id": result["name"], **zipped}
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
        if pending:
            yield from check_pending()


def _name_taken(response: httpx.Response) -> bool:
    """True if the registry refused a new resource because its name is taken."""
    if response.status_code != httpx.codes.BAD_REQUEST:
        return False
    try:
        return "name" in response.json()
    except ValueError:
        return False


def search(
    registry_url: str, /, superseded: bool | None = False, **params
) -> Iterator[dict]:
    """Yields the registry records that match the query params.

    Superseded resources are left out; superseded=True gives only them, and None
    gives both.
    """
    from nbank.registry import find_resource
    from nbank.util import query_registry_stream, registry_session

    if superseded is not None:
        params["superseded"] = superseded
    url, _ = find_resource(registry_url)
    with registry_session(registry_url) as session:
        yield from query_registry_stream(session, url, params)


def describe(registry_url: str, id: str) -> dict | None:
    """Returns the registry record for id, or None if it isn't registered."""
    from nbank.registry import get_resource
    from nbank.util import query_registry, registry_session

    url, params = get_resource(registry_url, id)
    with registry_session(registry_url) as session:
        return query_registry(session, url, params)


def describe_many(registry_url: str, *ids: str) -> Iterator[dict]:
    """Yields the registry record of each resource in ids that's registered."""
    from nbank.registry import get_resource_bulk
    from nbank.util import query_registry_bulk, registry_session

    url, query = get_resource_bulk(registry_url, ids)
    with registry_session(registry_url) as session:
        yield from query_registry_bulk(session, url, query)


def find_resources(
    registry_url: str,
    *ids: str,
    local_dir: Path | str | None = None,
    alt_base: Path | None = None,
    download: bool = True,
    verify: bool = False,
    auth: RegistryAuth = None,
) -> Iterator[tuple[str, Path | FileNotFoundError]]:
    """Yields (id, path) for each id, with the path of a file that can be read on
    this host, or a FileNotFoundError if there isn't one. Results come as they're
    ready, not in the order of ids.

    Each resource is looked for in local_dir (a directory of files named by id,
    such as a downloaded dataset) without asking the registry, and then at its
    locations in the order the registry gives them: an archive on this host, or
    for one that can be downloaded, the cache (see cache.cache_dir) and then a
    download into it. alt_base replaces the directory that contains each archive,
    for copies of archives on another host (see archive.Resource). download=False
    leaves out downloads, and verify=True checks each file against its registered
    hash. auth (default: .netrc) is used for the registry and for downloads.
    """
    import concurrent.futures

    from nbank.archive import resolve_extension
    from nbank.registry import get_locations_bulk
    from nbank.util import bulk_batch_size, query_registry_bulk, registry_session

    to_find = list(dict.fromkeys(ids))
    if local_dir is not None:
        for id in list(to_find):
            try:
                path = resolve_extension(Path(local_dir) / id)
            except FileNotFoundError:
                continue
            log.debug("%s: found in %s", id, local_dir)
            to_find.remove(id)
            yield id, path
    if not to_find:
        return
    remaining = set(to_find)
    with (
        registry_session(registry_url, auth=make_auth(auth)) as session,
        concurrent.futures.ThreadPoolExecutor() as executor,
    ):
        futures = {}
        for i in range(0, len(to_find), bulk_batch_size):
            url, query = get_locations_bulk(
                registry_url, to_find[i : i + bulk_batch_size]
            )
            for record in query_registry_bulk(session, url, query):
                future = executor.submit(
                    _find_resource,
                    session,
                    record,
                    alt_base=alt_base,
                    download=download,
                    verify=verify,
                )
                futures[future] = record["name"]
        for future in concurrent.futures.as_completed(futures):
            id = futures[future]
            remaining.discard(id)
            try:
                yield id, future.result()
            except FileNotFoundError as err:
                yield id, err
    for id in to_find:
        if id in remaining:
            yield (
                id,
                FileNotFoundError(f"{id} isn't in the registry or has no locations"),
            )


def find_resource(registry_url: str, id: str, **kwargs) -> Path:
    """Returns the path of a file for id that can be read on this host. Takes the
    options of find_resources, and raises FileNotFoundError if there isn't one."""
    # find_resources yields one result for each id
    _, result = next(find_resources(registry_url, id, **kwargs))
    if isinstance(result, FileNotFoundError):
        raise result
    return result


def _find_resource(
    session: httpx.Client,
    record: dict,
    *,
    alt_base: Path | None,
    download: bool,
    verify: bool,
) -> Path:
    """Returns the path of a file for a record from bulk/locations/, trying its
    locations in order (see find_resources). Raises FileNotFoundError if none works."""
    import os
    import tempfile

    from nbank.cache import cache_dir, host_dir
    from nbank.types import RemoteResource
    from nbank.util import hash, parse_location

    id = record["name"]

    def matches(path: Path) -> bool:
        if not verify or record["sha1"] is None:
            return True
        if hash(path) == record["sha1"]:
            return True
        log.warning("%s: %s doesn't match its registered hash", id, path)
        return False

    for location in record["locations"]:
        resource = parse_location(location, alt_base=alt_base, http_session=session)
        if isinstance(resource, RemoteResource):
            target_dir = cache_dir() / host_dir(resource.host)
            target = target_dir / record.get("filename", id)
            if target.exists():
                if matches(target):
                    log.debug("%s: found in the cache at %s", id, target)
                    return target
                target.unlink()
            if not download:
                continue
            log.debug("%s: downloading from %s", id, resource)
            target_dir.mkdir(parents=True, exist_ok=True)
            # a partial download never has the cached file's name
            fd, partial = tempfile.mkstemp(dir=target_dir, prefix=f".{id}.")
            os.close(fd)
            partial = Path(partial)
            try:
                resource.fetch(partial)
                if matches(partial):
                    partial.replace(target)
                    return target
            except httpx.HTTPError as err:
                log.warning("%s: unable to download from %s: %s", id, resource, err)
            finally:
                partial.unlink(missing_ok=True)
        elif (path := getattr(resource, "path", None)) is not None and path.exists():
            if matches(path):
                log.debug("%s: found in a local archive at %s", id, path)
                return path
    raise FileNotFoundError(
        f"{id} is registered, but isn't in an archive on this host or the cache"
        + (", and couldn't be downloaded" if download else "")
    )


def verify(registry_url: str, file: str | Path, id: str) -> bool:
    """Returns whether file's hash matches the registered hash of resource id.

    Raises ValueError if id isn't registered.
    """
    from nbank.util import hash

    file_hash = hash(Path(file))
    resource = describe(registry_url, id=id)
    if resource is None:
        raise ValueError(f"{id} does not exist")
    log.debug("verifying %s: registry %s; file %s", file, resource["sha1"], file_hash)
    return resource["sha1"] == file_hash


def find_by_hash(registry_url: str, file: str | Path) -> Iterator[dict]:
    """Hashes file and yields the registry records with that hash, superseded or not."""
    from nbank.util import hash

    file_hash = hash(Path(file))
    log.debug("searching for %s by hash (%s)", file, file_hash)
    return search(registry_url, sha1=file_hash, superseded=None)


def update(
    base_url: str,
    *ids: str,
    auth: RegistryAuth = None,
    metadata: Mapping[str, Any] | None = None,
    dtype: str | None = None,
) -> Iterator[dict]:
    """Update metadata for one or more resources. Set a key to None to delete.

    The experimenter and animal keys of metadata are the resources' fields; None
    clears them. dtype, if given, changes the resources' datatype.

    Yields the updated record for each resource. A resource that is not in the
    registry yields {"name": id, "error": "not found"}.
    """
    from nbank.registry import update_resource_metadata
    from nbank.util import registry_session

    with registry_session(base_url, auth=make_auth(auth)) as session:
        for id in ids:
            url, params = update_resource_metadata(base_url, id, **(metadata or {}))
            if dtype is not None:
                params["dtype"] = dtype
            r = session.patch(url, json=params)
            if r.status_code == 404:
                yield {"name": id, "error": "not found"}
                continue
            r.raise_for_status()
            yield r.json()


__all__ = [
    "deposit",
    "describe",
    "describe_many",
    "find_by_hash",
    "find_resource",
    "find_resources",
    "search",
    "update",
    "verify",
]

# Variables:
# End:
