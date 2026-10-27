# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""nbank subcommands that move resources between archives: copy, export and
archive import-tar."""

import datetime
import logging
import tarfile
from pathlib import Path

from nbank import (
    __version__,
    console,
    transfer,
    util,
)
from nbank.script import common

log = logging.getLogger("nbank")


def _record_location(
    session, registry_url, id, archive_name, received, restoring: bool
) -> None:
    """Adds archive_name as a location of a received resource, or if restoring, sets
    its key. Does nothing in a dry run. Raises TransferError if the registry refuses."""
    if received.path is None:
        return
    if restoring:
        transfer.set_location_key(
            session, registry_url, id, archive_name, received.path
        )
    else:
        transfer.add_location(session, registry_url, id, archive_name, received.path)


class _Stored:
    """Counts the resources stored in an archive (or in a dry run, checked), and
    restored, and logs each one."""

    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.n_stored = self.n_restored = self.total = self.restored_total = 0

    def add(self, label: str, received, restoring: bool, progress) -> None:
        if restoring:
            self.n_restored += 1
            self.restored_total += progress.nbytes
        else:
            self.n_stored += 1
            self.total += progress.nbytes
        result = received.path if not self.dry_run else "OK"
        note = "" if received.verified else " (no registered hash to check)"
        if restoring:
            note = " (restored)" + note
        log.info("  - %s -> %s%s  (%s)", label, result, note, progress.summary())

    def counts(self, verb: str, restore: bool) -> list[str]:
        """Returns the counts for the summary; verb is what was done to those stored."""
        counts = []
        if restore:
            counts.append(
                f"{'to restore' if self.dry_run else 'restored'}: {self.n_restored} "
                f"({console.human_size(self.restored_total)})"
            )
        counts.append(
            f"{'checked' if self.dry_run else verb}: {self.n_stored} "
            f"({console.human_size(self.total)})"
        )
        return counts


def _file_size(path: Path) -> int | None:
    """Returns the size of the file at path, or None if it can't be read."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def _log_unfound(sources) -> int:
    """Logs the sources with no copy to read. Returns how many there are."""
    n = 0
    for source in sources:
        if not source.ok:
            log.error("  ✗ %s -> %s", source.id, source.error)
            n += 1
    return n


def import_tar(args):
    """Import resources from a tar file, tape, or stdin into a neurobank archive.

    Each registered resource is checked against its hash as it's stored, then added as a
    location. With identifiers (or --from-file), imports only those resources and stops
    reading once they're found. With --dry-run, only reads and checks. Unregistered
    files and resources already in the destination are skipped; with --restore,
    resources the registry lists there whose files are missing are stored again.
    Returns 1 if anything fails to import, a requested resource isn't found, or the tar
    file can't be read.
    """
    n_failed = 0
    wanted = None
    if args.ids or args.from_file is not None:
        wanted = common.requested_ids(args)
        if wanted is None:
            return 1
        wanted = set(wanted)
    n_requested = None if wanted is None else len(wanted)
    archive_cfg = common.archive_config(args.dest)
    if archive_cfg is None:
        return 1
    registry_url = archive_cfg.registry
    archive_path = archive_cfg.path  # this will resolve the path
    log.info("registry: %s", registry_url)
    n_already = 0
    stored = _Stored(args.dry_run)
    with util.registry_session(registry_url, auth=args.auth) as session:
        archive_name = common.registered_name(session, registry_url, archive_path)
        if archive_name is None:
            return 1
        log.info("destination archive: %s (%s)", archive_name, archive_path)
        log.info("source: %s", args.tar)
        if args.dry_run:
            log.info("DRY RUN: checking resources without importing them")

        tar_lookup = common.tar_lookup(session, registry_url)

        def lookup(id, member):
            # only resources that were asked for are looked up
            if wanted is not None and id not in wanted:
                return None
            return tar_lookup(id, member)

        dest = None if args.dry_run else archive_cfg

        def import_one(res, progress) -> bool:
            """Imports a resource from the tar file. Returns False if it fails."""
            nonlocal n_already
            id = res.record["name"]
            restoring = False
            if archive_name in res.record["locations"]:
                if not args.restore or transfer.is_stored(archive_cfg, id):
                    log.info(
                        "  ✗ %s -> '%s' is already in the destination archive",
                        res.name,
                        id,
                    )
                    n_already += 1
                    return True
                restoring = True
            progress.start(res.name, res.size)
            try:
                received = transfer.receive_file(
                    dest, id, res.name, res.data, res.record["sha1"], progress=progress
                )
                _record_location(
                    session, registry_url, id, archive_name, received, restoring
                )
            except transfer.TarReadError:
                log.error("  ✗ %s -> unable to read; stopping", res.name)
                raise
            except transfer.TransferError as err:
                log.error("  ✗ %s -> %s", res.name, err)
                return False
            stored.add(res.name, received, restoring, progress)
            return True

        read_error = False
        try:
            with transfer.open_tar(args.tar) as tarf, console.Progress() as progress:
                for res in transfer.iter_tar_resources(tarf, lookup):
                    n_failed += not import_one(res, progress)
                    if wanted is not None:
                        wanted.discard(res.record["name"])
                        if not wanted:
                            # everything asked for is found; don't read the rest
                            break
        except (OSError, tarfile.TarError) as err:
            log.error("error: unable to read %s: %s", args.tar, err)
            n_failed += 1
            read_error = True
    for id in sorted(wanted or ()):
        reason = (
            "not reached, as reading stopped" if read_error else "not in the tar file"
        )
        log.error("  ✗ %s -> %s", id, reason)
        n_failed += 1
    counts = [] if n_requested is None else [f"requested: {n_requested}"]
    console.log_summary(
        *counts,
        *stored.counts("imported", args.restore),
        f"already in destination: {n_already}",
        failed=n_failed,
    )
    return 1 if n_failed else None


def _manifest_records(args, sources) -> dict | None:
    """Returns the registry records of sources for a manifest, by id.

    Returns None, after logging why, if a manifest can't be written.
    """
    for source in sources:
        if source.path.name == transfer.manifest_name:
            log.error(
                "error: '%s' is stored as %s, so there can't be a manifest",
                source.id,
                transfer.manifest_name,
            )
            return None
    existing = args.out / transfer.manifest_name
    if args.out.is_dir() and (existing.exists() or existing.is_symlink()):
        log.error("error: '%s' already exists", existing)
        return None
    ids = [source.id for source in sources]
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        records = common.records(session, args.registry_url, ids)
    return records


def _manifest_entry(source, record: dict | None) -> dict:
    """Returns the manifest entry for an exported resource.

    Locations are left out, as they describe where the resource is stored here.
    """
    entry = {"name": source.id, "path": source.path.name, "sha1": source.sha1}
    if record is not None:
        entry.update((k, v) for k, v in record.items() if k != "locations")
    return entry


def export_resources(args):
    """Write resources from this host's archives to a tar file, zip file, or directory.

    Resources that can't be read or don't match their hashes are left out. With
    --manifest, the registry records of those written are added last, as manifest.json.
    Returns 1 if anything is left out or the export can't be run.
    """
    if args.compress and args.out.suffix.lower() != ".zip":
        log.error("error: --compress only applies to zip files")
        return 1
    ids = common.requested_ids(args)
    if ids is None:
        return 1
    log.info("registry: %s", args.registry_url)
    n_failed = n_written = total = 0
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        sources = list(
            transfer.find_sources(
                session,
                args.registry_url,
                ids,
                archive=args.archive,
                alt_base=args.alt_base,
            )
        )
    n_requested = len(sources)

    def summarize():
        console.log_summary(
            f"requested: {n_requested}",
            f"exported: {n_written} ({console.human_size(total)})",
            failed=n_failed,
        )

    n_failed += _log_unfound(sources)
    sources = [source for source in sources if source.ok]
    if not sources:
        summarize()
        return 1
    if args.manifest:
        records = _manifest_records(args, sources)
        if records is None:
            return 1
        exported = []
    log.info("writing %d resource(s) to %s", len(sources), args.out)
    try:
        with (
            transfer.open_export(args.out, compress=args.compress) as writer,
            console.Progress() as progress,
        ):
            for source in sources:
                path = source.path
                assert path is not None  # sources without copies were left out
                progress.start(path.name, _file_size(path))
                try:
                    verified = transfer.write_resource(writer, source, progress)
                except transfer.TransferError as err:
                    log.error("  ✗ %s -> %s", source.id, err)
                    n_failed += 1
                    continue
                n_written += 1
                total += progress.nbytes
                if args.manifest:
                    exported.append(_manifest_entry(source, records.get(source.id)))
                note = "" if verified else " (no registered hash to check)"
                log.info(
                    "  - %s -> %s%s  (%s)",
                    source.id,
                    path.name,
                    note,
                    progress.summary(),
                )
            if args.manifest:
                transfer.write_manifest(
                    writer,
                    {
                        "registry": args.registry_url,
                        "exported": datetime.datetime.now(datetime.UTC).isoformat(
                            timespec="seconds"
                        ),
                        "nbank_version": __version__,
                        "resources": exported,
                    },
                )
                log.info("  - %s", transfer.manifest_name)
    except (OSError, ValueError, transfer.TransferError) as err:
        log.error("error: unable to export to %s: %s", args.out, err)
        return 1
    summarize()
    return 1 if n_failed else None


def copy_resources(args):
    """Copy resources from the archives on this host into another archive.

    Each is checked against its hash as it's stored, then added as a location. Resources
    already in the destination are skipped; with --restore, resources the registry
    lists there whose files are missing are stored again. With --dry-run, only reads
    and checks. Returns 1 if anything fails.
    """
    ids = common.requested_ids(args)
    if ids is None:
        return 1
    archive_cfg = common.archive_config(args.dest)
    if archive_cfg is None:
        return 1
    registry_url = archive_cfg.registry
    archive_path = archive_cfg.path
    log.info("registry: %s", registry_url)
    stored = _Stored(args.dry_run)
    with util.registry_session(registry_url, auth=args.auth) as session:
        dest_name = common.registered_name(session, registry_url, archive_path)
        if dest_name is None:
            return 1
        if args.archive == dest_name:
            log.error("error: the source and destination archives are the same")
            return 1
        log.info("destination archive: %s (%s)", dest_name, archive_path)
        if args.dry_run:
            log.info("DRY RUN: checking resources without copying them")
        already = transfer.located_in(session, registry_url, ids, dest_name)
        missing = set()
        if args.restore:
            missing = {id for id in already if not transfer.is_stored(archive_cfg, id)}
            already -= missing
        for id in ids:
            if id in already:
                log.info("  - %s -> already in the destination archive", id)
        sources = list(
            transfer.find_sources(
                session,
                registry_url,
                [id for id in ids if id not in already],
                archive=args.archive,
                alt_base=args.alt_base,
            )
        )
        n_failed = _log_unfound(sources)
        dest = None if args.dry_run else archive_cfg
        with console.Progress() as progress:
            for source in sources:
                path = source.path
                if path is None:
                    continue
                progress.start(path.name, _file_size(path))
                restoring = source.id in missing
                try:
                    received = transfer.receive_source(dest, source, progress)
                    _record_location(
                        session, registry_url, source.id, dest_name, received, restoring
                    )
                except transfer.TransferError as err:
                    log.error("  ✗ %s -> %s", source.id, err)
                    n_failed += 1
                    continue
                stored.add(source.id, received, restoring, progress)
    console.log_summary(
        f"requested: {len(ids)}",
        *stored.counts("copied", args.restore),
        f"already in destination: {len(already)}",
        failed=n_failed,
    )
    return 1 if n_failed else None
