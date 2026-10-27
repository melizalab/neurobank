# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""nbank subcommands for resources and the registry: deposit, locate, search, info,
modify, fetch, verify, registry info and dtype."""

import concurrent.futures
import json
import logging
import sys
import tarfile
from pathlib import Path

import httpx

from nbank import (
    core,
    readers,
    registry,
    transfer,
    util,
)
from nbank.script import common
from nbank.types import LocalResource

log = logging.getLogger("nbank")


def registry_info(args):
    log.info("registry info:")
    log.info("  - address: %s", args.registry_url)
    url, params = registry.get_info(args.registry_url)
    # no version check, so this shows any registry's API version
    with util.registry_session(
        args.registry_url, auth=args.auth, check_version=False
    ) as session:
        info = util.query_registry(session, url, params)
    if info is None:
        log.error("error: %s is not a neurobank registry (no info/)", args.registry_url)
        return 1
    # registries before API 1.3 don't say who the request is from
    has_user = "user" in info
    user = info.pop("user", None)
    for k, v in info.items():
        log.info("  - %s: %s", k, v)
    if not has_user:
        return
    if user is None:
        log.info("  - user: anonymous (no credentials; use -a or a .netrc file)")
    elif user["superuser"]:
        log.info("  - user: %s (superuser)", user["username"])
    else:
        log.info("  - user: %s", user["username"])
        log.info("  - permissions: %s", ", ".join(user["permissions"]) or "none")


def list_datatypes(args):
    url, params = registry.get_datatypes(args.registry_url)
    installed = readers.installed_readers()
    with util.registry_session(args.registry_url) as session:
        dtypes = list(util.query_registry_paginated(session, url, params))
    for dtype in dtypes:
        line = f"{dtype['name']:<25}\t({dtype['content_type']})"
        if dtype["name"] in installed:
            line += f"\treader: {installed[dtype['name']]}"
        print(line)


def add_datatype(args):
    url, params = registry.add_datatype(
        args.registry_url, args.dtype_name, args.content_type
    )
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        resp = session.post(url, json=params)
    resp.raise_for_status()
    data = resp.json()
    log.info(f"added datatype {data['name']} (content-type: {data['content_type']})")


def core_field_args(args) -> dict:
    """Returns the values of --experimenter and --animal that were given"""
    return {
        field: value
        for field in registry.core_fields
        if (value := getattr(args, field)) is not None
    }


def store_resources(args):
    if args.read_stdin:
        args.file.extend(Path(name) for name in common.stripped_lines(sys.stdin))
    n_failed = 0
    try:
        for res in core.deposit(
            args.directory,
            args.file,
            dtype=args.dtype,
            hash=args.hash,
            auto_id=args.auto_id,
            auth=args.auth,
            dry_run=args.dry_run,
            skip_errors=True,
            read_metadata=args.read_metadata,
            override_file_metadata=args.override_file_metadata,
            zip_directories=args.zip_directories,
            metadata={**args.metadata, **core_field_args(args)},
        ):
            n_failed += "error" in res
            if args.json_out:
                json.dump(res, fp=sys.stdout, cls=util.JSONEncoder)
                sys.stdout.write("\n")
    except (ValueError, OSError, RuntimeError) as e:
        log.error("error: %s", e)
        return 1
    if n_failed:
        log.error("files that could not be deposited: %d", n_failed)
        return 1


def locate_resources(args):
    """Returns 1 if any id can't be resolved to a location reachable from this host."""
    # This subcommand can handle IDs or full neurobank URLs
    n_failed = 0
    with util.registry_session() as session:
        for id in args.id:
            try:
                base, id = registry.parse_resource_url(id)
            except ValueError:
                base = args.registry_url
            if base is None:
                print(f"{id:<20} [no registry to resolve short identifier]")
                n_failed += 1
                continue
            util.check_registry_version(session, base)
            url, params = registry.get_locations(base, id)
            found = False
            try:
                locations = util.query_registry_paginated(session, url, params)
                for loc in locations:
                    resource = util.parse_location(loc, alt_base=args.alt_base)
                    if resource is None:
                        pass
                    elif args.link is not None:
                        if not isinstance(resource, LocalResource):
                            log.info("%s doesn't support linking", resource)
                            continue
                        try:
                            linkpath = resource.link(args.link)
                        except OSError as err:
                            log.error("%s: unable to link: %s", id, _link_error(err))
                            break
                        print(f"{id:<20}\t-> {linkpath}")
                        found = True
                        break
                    elif args.print0:
                        if isinstance(resource, LocalResource):
                            print(str(resource.path), end="\0")
                            found = True
                        else:
                            log.info("%s isn't local, skipping", resource)
                    else:
                        print(f"{id:<20}\t{resource}")
                        found = True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    print(f"{id:<20}\t(not found)")
                else:
                    registry.log_error(e)
            n_failed += not found
    return 1 if n_failed else None


def _link_error(err: OSError) -> str:
    # ERROR_PRIVILEGE_NOT_HELD
    if getattr(err, "winerror", None) == 1314:
        return (
            "Windows creates symbolic links only in Developer Mode or with "
            "administrator rights"
        )
    return str(err)


def search_resources(args):
    # parse commandline args to query dict
    argmap = [
        ("name", "name"),
        ("dtype", "dtype"),
        ("archive", "archive"),
        ("scheme", "scheme"),
    ]
    params = {
        paramname: getattr(args, argname)
        for (paramname, argname) in argmap
        if getattr(args, argname) is not None
    }
    if args.hash is not None:
        params.update(registry.hash_search_param(args.hash))
    if args.experimenter is not None:
        params["experimenter"] = args.experimenter
    if args.animal is not None:
        # the first part of a UUID is enough
        params["animal__istartswith"] = args.animal
    for k, v in args.metadata.items():
        params[f"metadata__{k}"] = v
    for k, v in args.metadata_neq.items():
        params[f"metadata__{k}__neq"] = v
    if len(params) == 0 and args.superseded is not True:
        log.error("error: at least one filter parameter is required")
        return 1
    for d in core.search(args.registry_url, superseded=args.superseded, **params):
        if args.json_out:
            json.dump(d, fp=sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(d["name"])


def get_resource_info(args):
    # missing ids just get skipped by the server, so we track which have not
    # been returned
    results = {id: {"id": id, "error": "not found"} for id in args.id}
    for result in core.describe_many(args.registry_url, *args.id):
        results[result["name"]] = result
    for _, result in results.items():
        json.dump(result, fp=sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 1 if any("error" in result for result in results.values()) else None


def set_resource_metadata(args):
    for key in args.metadata_remove:
        args.metadata[key] = None
    # an empty value clears a field
    fields = {k: v or None for k, v in core_field_args(args).items()}
    n_failed = 0
    for result in core.update(
        args.registry_url,
        *args.id,
        auth=args.auth,
        metadata={**args.metadata, **fields},
        dtype=args.dtype,
    ):
        json.dump(result, fp=sys.stdout, indent=2)
        sys.stdout.write("\n")
        n_failed += "error" in result
    return 1 if n_failed else None


def fetch_resources(args):
    dest = args.dest or Path()
    if args.read_stdin and str(args.tar) == "-":
        log.error("error: -@ and --tar - both read standard input")
        return 1
    to_fetch = set(args.ids)
    if args.read_stdin:
        to_fetch.update(common.stripped_lines(sys.stdin))
    if not to_fetch:
        log.error("error: no identifiers given")
        return 1
    if args.tar is not None:
        return _fetch_from_tar(args, dest, to_fetch)
    url, query = registry.get_locations_bulk(args.registry_url, sorted(to_fetch))
    with (
        util.registry_session(args.registry_url, auth=args.auth) as session,
        concurrent.futures.ThreadPoolExecutor() as executor,
    ):
        response = tuple(util.query_registry_bulk(session, url, query))
        to_fetch -= {resource["name"] for resource in response}
        if args.verify:
            for resource in response:
                if resource["sha1"] is None:
                    log.warning("%s: no registered hash to check", resource["name"])
        future_to_name = {
            executor.submit(
                util.fetch_resource,
                session,
                resource["locations"],
                # try filename if the server returns it
                (dest / resource.get("filename", resource["name"])),
                extension=args.extension,
                force=args.force,
                alt_base=args.alt_base,
                sha1=resource["sha1"] if args.verify else None,
            ): resource["name"]
            for resource in response
        }
        n_failed = len(to_fetch)
        for future in concurrent.futures.as_completed(future_to_name):
            resource_id = future_to_name[future]
            result = future.result()
            print(f"{resource_id:<20}\t-> {result}")
            n_failed += isinstance(result, Exception)
    for resource_id in to_fetch:
        print(f"{resource_id:<20}\t-> (no locations found)")
    return 1 if n_failed else None


def _fetch_from_tar(args, dest: Path, to_fetch: set[str]) -> int | None:
    """Writes the resources in to_fetch from the tar file args.tar into dest, named
    as fetch names them, reading the tar file once. Returns 1 if any fail."""
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        records = common.records(session, args.registry_url, sorted(to_fetch))
    n_failed = 0
    for id in sorted(to_fetch - records.keys()):
        print(f"{id:<20}\t-> (not in the registry)")
        n_failed += 1
    remaining = set(records)

    def lookup(id, member):
        # the first copy of each resource asked for
        return records[id] if id in remaining else None

    read_error = False
    try:
        with transfer.open_tar(args.tar) as tarf:
            for res in transfer.iter_tar_resources(tarf, lookup):
                id = res.record["name"]
                remaining.discard(id)
                target = dest / res.record["filename"]
                if args.extension:
                    target = target.with_suffix(f".{args.extension}")
                if target.exists() and not args.force:
                    result = f"(target file {target} already exists)"
                    n_failed += 1
                else:
                    try:
                        # reading from tape is slow, so keep data that doesn't
                        # match for the user to judge
                        received = transfer.write_file(
                            target,
                            res.data,
                            res.record["sha1"],
                            replace=args.force,
                            keep_mismatched=target.with_name(f"{target.name}.mismatch"),
                        )
                    except transfer.TarReadError:
                        print(f"{id:<20}\t-> (unable to read; stopping)")
                        n_failed += 1
                        raise
                    except transfer.TransferError as err:
                        result = f"({err})"
                        n_failed += 1
                    else:
                        result = str(received.path)
                        if not received.verified:
                            result += " (no registered hash to check)"
                print(f"{id:<20}\t-> {result}")
                if not remaining:
                    # everything asked for is found; don't read the rest
                    break
    except (OSError, tarfile.TarError) as err:
        log.error("error: unable to read %s: %s", args.tar, err)
        read_error = True
    for id in sorted(remaining):
        reason = (
            "not reached, as reading stopped" if read_error else "not in the tar file"
        )
        print(f"{id:<20}\t-> ({reason})")
        n_failed += 1
    return 1 if n_failed or read_error else None


def verify_file_hash(args):
    """Returns 1 if any file is missing or doesn't match a registry record, or
    with --metadata, if its metadata doesn't match or can't be read."""
    from nbank.util import id_from_fname

    n_failed = 0
    found_readers = {}
    for path in args.files:
        if not path.exists():
            print(f"{path}: no such file or directory")
            n_failed += 1
            continue
        if path.is_dir():
            print(f"{path}: is a directory, which isn't a resource")
            n_failed += 1
            continue
        # if the name isn't a registered id, look for the file's hash instead
        matched = []
        try:
            test_id = id_from_fname(path)
            if core.verify(args.registry_url, path, test_id):
                print(f"{path}: OK")
                if args.metadata:
                    record = core.describe(args.registry_url, test_id)
                    if record is not None:
                        matched.append(record)
            else:
                print(f"{path}: FAILED to match record for {test_id}")
                n_failed += 1
        except ValueError:
            for resource in core.find_by_hash(args.registry_url, path):
                print(f"{path}: matches registry resource {resource['name']}")
                matched.append(resource)
            if not matched:
                print(f"{path}: no matches in registry")
                n_failed += 1
        if args.metadata:
            for record in matched:
                if not check_file_metadata(path, record, found_readers):
                    n_failed += 1
    return 1 if n_failed else None


def check_file_metadata(path: Path, record: dict, found_readers: dict) -> bool:
    """Prints whether the metadata that the reader for record's dtype reads from
    path matches the record. Returns False if it doesn't, or can't be read.
    found_readers caches the reader for each dtype."""
    dtype = record.get("dtype")
    if dtype is None:
        print(f"{path}: no dtype registered; metadata not checked")
        return True
    if dtype not in found_readers:
        try:
            found_readers[dtype] = readers.find_reader(dtype)
        except RuntimeError as err:
            found_readers[dtype] = err
    reader = found_readers[dtype]
    if isinstance(reader, RuntimeError):
        print(f"{path}: metadata FAILED: {reader}")
        return False
    if reader is None:
        print(f"{path}: no metadata reader for {dtype}; metadata not checked")
        return True
    try:
        from_file = readers.read_file_metadata(reader, dtype, path)
    except ValueError as err:
        print(f"{path}: metadata FAILED: {err}")
        return False
    log.debug("   from the file: %s", from_file)
    conflicts = readers.compare_metadata(from_file, readers.registered_metadata(record))
    if conflicts:
        print(
            f"{path}: metadata FAILED to match record for {record['name']} "
            f"({'; '.join(conflicts)})"
        )
        return False
    print(f"{path}: metadata OK")
    return True
