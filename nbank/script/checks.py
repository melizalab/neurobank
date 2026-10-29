# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""nbank check subcommands: registry, archive, all, superseded and tar."""

import logging
import sys
import tarfile
from collections import Counter
from pathlib import Path

import httpx

from nbank import (
    archive,
    check,
    console,
    readers,
    registry,
    transfer,
    util,
)
from nbank.script import common

log = logging.getLogger("nbank")


def check_archive(args):
    """Check an archive against the registry and its policy.

    Files must be readable and match their registry records and hashes, every resource
    registered to the archive must have a file, the key of each resource's location
    must be the name of its file, and ownership and permissions must follow the policy
    (keys and permissions are fixed with --fix). The archive is a path if it's an
    existing directory, and otherwise a name in the registry. A path is checked against
    the registry given with -r, or else the one in its nbank.json. Returns 1 if there
    are errors that weren't fixed or the check couldn't be run.
    """
    path = Path(args.archive)
    if not path.is_dir():
        if common.not_a_name(args.archive):
            return 1
        return _check_archive_by_name(args, args.archive)
    archive_cfg = common.archive_config(path)
    if archive_cfg is None:
        return 1
    log.info("archive: %s", archive_cfg.path)
    registry_url = args.registry_url if args.registry_given else archive_cfg.registry
    log.info("registry: %s", registry_url)
    common.warn_other_registry(archive_cfg, registry_url)
    with util.registry_session(registry_url, auth=args.auth) as session:
        name = common.registered_name(session, registry_url, archive_cfg.path)
        if name is None:
            return 1
        ok = _check_archive(session, registry_url, name, archive_cfg, args)
    return 0 if ok else 1


def _check_archive_by_name(args, name: str) -> int:
    """Checks the archive with this name in the registry. It must be a neurobank archive
    on this host."""
    registry_url = args.registry_url
    if registry_url is None:
        log.error(
            "error: supply a registry url with '-r' or %s environment variable, "
            "or the path of the archive",
            registry._env_registry,
        )
        return 1
    log.info("registry: %s", registry_url)
    with util.registry_session(registry_url, auth=args.auth) as session:
        url, _ = registry.get_archive(registry_url, name)
        info = util.query_registry(session, url)
        if info is None:
            log.error(
                "error: '%s' is not a directory or an archive in the registry", name
            )
            return 1
        if info["scheme"] not in archive.Resource.schemes:
            log.error("error: checking %s archives isn't supported", info["scheme"])
            return 1
        if not Path(info["root"]).is_dir():
            log.error("error: %s is not on this host", info["root"])
            return 1
        ok = _check_registered_archive(session, registry_url, info, args)
    return 0 if ok else 1


def _check_registered_archive(session, registry_url, info, args) -> bool:
    """Checks a neurobank archive on this host, given its registry record: its
    configuration must match the record, then its contents are checked. Returns True if
    there are no errors."""
    name = info["name"]
    root = Path(info["root"])
    log.info("\narchive %s: %s", name, root)
    try:
        archive_cfg = archive.ArchiveConfig.load(root)
    except FileNotFoundError:
        log.error(" - %s is not a valid neurobank archive", root)
        return False
    except (OSError, ValueError, KeyError) as err:
        log.error(" - unable to read nbank.json: %s", err)
        return False
    ok = True
    if archive_cfg.path != root:
        log.error(
            " - registered root resolves to %s; deposits won't find the archive",
            archive_cfg.path,
        )
        ok = False
    common.warn_other_registry(archive_cfg, registry_url)
    try:
        if not _check_archive(session, registry_url, name, archive_cfg, args):
            ok = False
    except OSError as err:
        log.error(" - unable to check: %s", err)
        ok = False
    return ok


def _check_archive(session, registry_url, archive_name, archive_cfg, args) -> bool:
    """Runs and logs the archive checks. Returns True if there are no errors."""
    run = _ArchiveCheck(session, registry_url, archive_name, archive_cfg, args)
    run.check_permissions()
    run.check_contents()
    if args.fix and args.rename and run.other_names:
        run.rename_files()
    run.check_keys()
    run.log_other_names()
    prompting = args.fix and console.interactive()
    if args.fix and (run.unregistered or run.incomplete) and not prompting:
        log.info("not an interactive terminal, so not asking how to resolve problems")
    run.resolve_incomplete(prompting)
    run.resolve_unregistered(prompting)
    run.log_summary()
    return run.n_errors == 0 and not run.unable_to_check


class _ArchiveCheck:
    """The phases of checking a neurobank archive, which log as they go.

    The registry's records for the archive are read on creation. Errors are counted
    in n_errors, and findings in counts by status, for the summary.
    """

    def __init__(self, session, registry_url, archive_name, archive_cfg, args):
        self.session = session
        self.registry_url = registry_url
        self.archive_name = archive_name
        self.archive_cfg = archive_cfg
        self.args = args
        log.info(
            "retrieving resources that should be in %s from the registry...",
            archive_name,
        )
        self.located = check.registry_locations_in_archive(
            session, registry_url, archive_name
        )
        log.info(" - resources in the registry: %d", len(self.located))
        self.keys = {name: loc["key"] for name, loc in self.located.items()}
        self.dtype_readers = None
        if not args.no_hash and readers.installed_readers():
            url, params = registry.get_datatypes(registry_url)
            self.dtype_readers = check.DtypeReaders(
                {
                    d["name"]: d.get("extension")
                    for d in util.query_registry_paginated(session, url, params)
                }
            )
        self.counts = Counter()
        self.n_errors = 0
        self.unable_to_check = False
        self.perm_counts = Counter()
        self.n_perms_fixed = 0
        self.unregistered = []  # looked up in the registry after the main pass
        self.incomplete = []  # left over from transfers; may be deleted after it
        self.wrong_keys = []  # set after the main pass with --fix
        self.other_names = []  # (resource, finding, filename) for files named otherwise
        self.invalid = set()  # resources whose files their dtype's reader rejects
        self.key_counts = Counter()
        self.n_renamed = self.n_keys_set = self.n_deleted = self.n_resolved = 0

    def check_permissions(self) -> None:
        """Checks (and with --fix, fixes) the ownership and permissions of the files."""
        args = self.args
        log.info("checking ownership and permissions:")
        try:
            for finding in check.check_archive_permissions(
                self.archive_cfg, fix=args.fix
            ):
                self.perm_counts[finding.status, finding.fixed] += 1
                if not args.verbose:
                    continue
                msg = f" - {finding.path} - {finding.status.value} ({finding.detail})"
                if finding.fixed:
                    log.info("%s - fixed", msg)
                else:
                    log.error("%s", msg)
        except ValueError as err:
            log.error(" - unable to check: %s", err)
            self.unable_to_check = True
        if not args.verbose:
            for (status, fixed), count in self.perm_counts.items():
                if fixed:
                    log.info(
                        " - %s: fixed %d files or directories", status.value, count
                    )
                else:
                    log.error(
                        " - %s: %d files or directories (use -v to list)",
                        status.value,
                        count,
                    )
        self.n_perms_fixed = sum(
            n for (_, fixed), n in self.perm_counts.items() if fixed
        )
        self.n_errors += self.perm_counts.total() - self.n_perms_fixed

    def check_contents(self) -> None:
        """Checks the files against the registry, logging most problems and keeping
        the rest (keys, names, incomplete and unregistered files) for later phases."""
        args = self.args
        log.info("verifying resources:")
        for finding in check.check_archive_contents(
            self.archive_cfg.path,
            {name: loc["sha1"] for name, loc in self.located.items()},
            check_hash=not args.no_hash,
            keys=self.keys,
            dtypes={name: loc["dtype"] for name, loc in self.located.items()},
            dtype_readers=self.dtype_readers,
        ):
            if finding.status in (check.Status.NO_KEY, check.Status.WRONG_KEY):
                self.wrong_keys.append(finding)
                continue
            if finding.status == check.Status.INVALID_FOR_DTYPE:
                self.invalid.add(finding.resource)
                self.counts[finding.status] += 1
                self.n_errors += 1
                log.error(
                    " - %s : %s - %s (%s)",
                    finding.resource,
                    finding.path,
                    finding.status.value,
                    finding.detail,
                )
                continue
            if finding.status == check.Status.READER_FAILED:
                self.counts[finding.status] += 1
                self.n_errors += 1
                log.error(" - %s: %s", finding.status.value, finding.detail)
                continue
            resource = finding.resource
            if resource in self.located and finding.path is not None:
                filename = self.located[resource]["filename"]
                if finding.path.name != filename:
                    self.other_names.append((resource, finding, filename))
            if finding.status == check.Status.MISSING_FROM_REGISTRY:
                self.unregistered.append(finding)
                continue
            if finding.status == check.Status.INCOMPLETE:
                self.incomplete.append(finding)
                continue
            self.counts[finding.status] += 1
            self.n_errors += not finding.ok
            if finding.status == check.Status.MISSING_FROM_ARCHIVE:
                log.error(" - %s: MISSING from the archive", finding.resource)
            elif finding.resource is None:
                log.error(
                    " - %s - %s (%s)",
                    finding.path,
                    finding.status.value,
                    finding.detail,
                )
            elif not finding.ok:
                log.error(
                    " - %s : %s - %s",
                    finding.resource,
                    finding.path,
                    finding.status.value,
                )
            elif args.verbose:
                log.info(
                    " - %s : %s - %s",
                    finding.resource,
                    finding.path,
                    finding.status.value,
                )

    def rename_files(self) -> None:
        """Renames files to the registry's filename, except invalid ones, and updates
        the key findings to match."""
        log.info("renaming files to the registry's filename:")
        renamed = {}
        for resource, finding, filename in self.other_names:
            if resource in self.invalid:
                log.info(
                    " - %s : not renamed, as it isn't a valid file of its dtype",
                    finding.path,
                )
                continue
            try:
                path = check.rename_to_filename(finding, filename)
            except OSError as err:
                log.error(" - %s : unable to rename: %s", finding.path, err)
                self.n_errors += 1
                continue
            if path is not None:
                renamed[resource] = path
        self.n_renamed = len(renamed)
        self.other_names = [e for e in self.other_names if e[0] not in renamed]
        self.wrong_keys = check.keys_after_renames(self.wrong_keys, renamed, self.keys)

    def check_keys(self) -> None:
        """Reports location keys that don't match the file names, and with --fix sets
        them, except for invalid files."""
        args = self.args
        if self.wrong_keys:
            log.info("checking location keys:")
        if args.fix:
            to_set = [f for f in self.wrong_keys if f.resource not in self.invalid]
            refused = dict(
                check.set_keys(
                    self.session, self.registry_url, self.archive_name, to_set
                )
            )
        for finding in self.wrong_keys:
            assert finding.path is not None  # NO_KEY and WRONG_KEY findings are files
            msg = f" - {finding.resource} : {finding.path} - {finding.status.value}"
            if finding.detail:
                msg += f" ({finding.detail})"
            if args.fix and finding.resource in self.invalid:
                log.error("%s - not fixed, as it isn't a valid file of its dtype", msg)
            elif args.fix:
                error = refused.get(finding.resource)
                if error is None:
                    self.n_keys_set += 1
                    if args.verbose:
                        log.info("%s - fixed", msg)
                    continue
                log.error(
                    " - %s : unable to set the key to '%s': %s",
                    finding.resource,
                    finding.path.name,
                    error,
                )
            elif args.verbose:
                log.error("%s", msg)
            self.key_counts[finding.status] += 1
            self.n_errors += 1
        if not args.verbose:
            for status, count in self.key_counts.items():
                log.error(" - %s: %d resources (use -v to list)", status.value, count)

    def log_other_names(self) -> None:
        """Reports the files named other than the registry's filename (not errors)."""
        verbose = self.args.verbose
        log.info(
            "files named other than the registry's filename: %d%s",
            len(self.other_names),
            "" if verbose or not self.other_names else " (use -v to list)",
        )
        if verbose:
            for _, finding, filename in self.other_names:
                log.info(" - %s (filename: %s)", finding.path, filename)

    def resolve_incomplete(self, prompting: bool) -> None:
        """Reports files left by incomplete transfers, offering to delete them."""
        for finding in self.incomplete:
            assert finding.path is not None  # INCOMPLETE findings are files
            log.error(
                " - %s - %s (%s)", finding.path, finding.status.value, finding.detail
            )
            if prompting and console.ask("   [d]elete, [s]kip? ", "ds") == "d":
                try:
                    archive.remove(finding.path)
                except OSError as err:
                    log.error("   unable to delete %s: %s", finding.path, err)
                else:
                    log.info("   - deleted %s", finding.path)
                    self.n_deleted += 1
                    continue
            self.counts[finding.status] += 1
            self.n_errors += 1

    def resolve_unregistered(self, prompting: bool) -> None:
        """Looks up the files that aren't in the archive's records in the rest of the
        registry, offering to resolve those registered elsewhere."""
        if self.unregistered:
            log.info("looking up files that aren't in this archive's registry records:")
        for finding in check.recheck_unregistered(
            self.session, self.registry_url, self.unregistered
        ):
            if finding.status == check.Status.MISSING_FROM_REGISTRY:
                log.error(
                    " - %s: MISSING from the registry under %s",
                    finding.path,
                    self.archive_name,
                )
            else:
                log.error(
                    " - %s: %s (%s)", finding.path, finding.status.value, finding.detail
                )
                if prompting and _resolve_elsewhere(
                    self.session, self.registry_url, self.archive_name, finding
                ):
                    self.n_resolved += 1
                    continue
            self.counts[finding.status] += 1
            self.n_errors += 1

    def log_summary(self) -> None:
        """Logs the metadata reader counts, then the totals of each kind of problem."""
        args = self.args
        counts = self.counts
        if args.no_hash:
            log.info(
                "files not checked against their dtypes' metadata readers (--no-hash)"
            )
        elif self.dtype_readers is None:
            log.info("no metadata readers installed, so no files checked against them")
        else:
            log.info(
                "files checked with their dtype's metadata reader: %d; "
                "with no reader installed for their dtype: %d",
                self.dtype_readers.checked.total(),
                self.dtype_readers.unchecked.total(),
            )
        log.info(
            "\nResources in registry: %d; missing from archive: %d; missing from registry: %d; read/verify errors: %d; other layout errors: %d; permission errors: %d; registered elsewhere: %d; location key errors: %d; invalid for dtype: %d",
            len(self.located),
            counts[check.Status.MISSING_FROM_ARCHIVE],
            counts[check.Status.MISSING_FROM_REGISTRY],
            counts[check.Status.UNREADABLE] + counts[check.Status.HASH_MISMATCH],
            counts[check.Status.MISPLACED]
            + counts[check.Status.DUPLICATE]
            + counts[check.Status.UNEXPECTED]
            + counts[check.Status.SYMLINK]
            + counts[check.Status.DIRECTORY]
            + counts[check.Status.INCOMPLETE],
            self.perm_counts.total() - self.n_perms_fixed,
            counts[check.Status.REGISTERED_ELSEWHERE]
            + counts[check.Status.UNVERIFIED_ELSEWHERE]
            + counts[check.Status.CHANGED_ELSEWHERE],
            self.key_counts.total(),
            counts[check.Status.INVALID_FOR_DTYPE],
        )
        if args.fix:
            log.info("Permission errors fixed: %d", self.n_perms_fixed)
            log.info("Location keys set: %d", self.n_keys_set)
            if args.rename:
                log.info("Files renamed: %d", self.n_renamed)
            log.info("Files registered elsewhere resolved: %d", self.n_resolved)
            log.info("Incomplete transfers and deposits deleted: %d", self.n_deleted)


def _resolve_elsewhere(session, registry_url, archive_name, finding) -> bool:
    """Offers to delete a file registered elsewhere, or to add its location.

    Only for files that match the registered hash or whose resource has none. Deleting
    is offered only if the resource has another location, and adding only if the file is
    in the right subdirectory. Returns True if the file was deleted or the location
    added.
    """
    if finding.status not in (
        check.Status.REGISTERED_ELSEWHERE,
        check.Status.UNVERIFIED_ELSEWHERE,
    ):
        return False
    path = finding.path
    options = []
    if finding.locations:
        options.append(("d", "[d]elete this copy"))
    if path.parent.name == archive.id_stub(finding.resource):
        options.append(("a", f"[a]dd {archive_name} as a location"))
    if not options:
        return False
    if finding.status == check.Status.UNVERIFIED_ELSEWHERE:
        log.warning(
            "   the registry has no hash for %s, so this file may not be a copy",
            finding.resource,
        )
    options.append(("s", "[s]kip"))
    choices = "".join(key for key, _ in options)
    answer = console.ask(f"   {', '.join(text for _, text in options)}? ", choices)
    if answer == "d":
        try:
            archive.remove(path)
        except OSError as err:
            log.error("   unable to delete %s: %s", path, err)
            return False
        log.info("   - deleted %s", path)
        return True
    if answer == "a":
        url, body = registry.add_location(
            registry_url, finding.resource, archive_name, path.name
        )
        r = session.post(url, json=body)
        if r.status_code != httpx.codes.CREATED:
            log.error("   unable to add location: %s", r.text)
            return False
        log.info("   - added %s as a location for %s", archive_name, finding.resource)
        return True
    return False


def check_registry(args):
    """Check the registry for resources without locations and empty archives.

    Returns 1 if any resource has no locations or the check couldn't be run, 0
    otherwise.
    """
    log.info("registry: %s", args.registry_url)
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        ok = _check_registry(session, args.registry_url)
    return 0 if ok else 1


def check_superseded(args):
    """Report superseded and unregistered resources among args.ids.

    Returns 1 if any is superseded or unregistered.
    """
    ids = list(args.ids)
    if args.read_stdin:
        ids.extend(common.stripped_lines(sys.stdin))
    ids = list(dict.fromkeys(ids))
    if not ids:
        log.error("error: no identifiers given")
        return 1
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        records = common.records(session, args.registry_url, ids)
    n_superseded = n_missing = 0
    for id in ids:
        record = records.get(id)
        if record is None:
            log.error(" - %s: not in the registry", id)
            n_missing += 1
        elif record["superseded_on"] is not None:
            details = [f"superseded on {record['superseded_on']}"]
            if record["superseded_by"]:
                details.append(f"by {record['superseded_by']}")
            if record["superseded_note"]:
                details.append(f"({record['superseded_note']})")
            log.error(" - %s: %s", id, " ".join(details))
            n_superseded += 1
    log.info(
        "%d resources checked; superseded: %d; not in the registry: %d",
        len(ids),
        n_superseded,
        n_missing,
    )
    return 1 if n_superseded or n_missing else 0


def _check_registry(session, registry_url) -> bool:
    """Runs and logs the registry checks. Returns True if there are no errors."""
    counts = Counter()
    for finding in check.check_registry(session, registry_url):
        counts[finding.status] += 1
        if finding.status == check.Status.NO_LOCATION:
            log.error(" - %s: %s", finding.resource, finding.status.value)
        else:
            log.warning(" - archive %s: %s", finding.archive, finding.status.value)
    log.info(
        "\nResources without locations: %d; empty archives: %d",
        counts[check.Status.NO_LOCATION],
        counts[check.Status.EMPTY_ARCHIVE],
    )
    return counts[check.Status.NO_LOCATION] == 0


def check_all(args):
    """Check the registry, then every neurobank archive on this host.

    Other archives are skipped, and a problem with one archive doesn't stop the rest.
    Returns 1 if the registry or any archive has errors.
    """
    registry_url = args.registry_url
    log.info("registry: %s", registry_url)
    failed = []
    skipped = []
    with util.registry_session(registry_url, auth=args.auth) as session:
        if not _check_registry(session, registry_url):
            failed.append("(registry)")
        url, params = registry.get_archives(registry_url)
        for info in util.query_registry_paginated(session, url, params):
            name = info["name"]
            root = Path(info["root"])
            if info["scheme"] not in archive.Resource.schemes:
                skipped.append((name, f"{info['scheme']} archive"))
                continue
            if not root.is_dir():
                skipped.append((name, f"{root} is not on this host"))
                continue
            if not _check_registered_archive(session, registry_url, info, args):
                failed.append(name)
    log.info("\nskipped archives: %d", len(skipped))
    for name, reason in skipped:
        log.info(" - %s: %s", name, reason)
    if failed:
        log.error("failed checks: %s", ", ".join(failed))
    else:
        log.info("all checks passed")
    return 1 if failed else 0


def check_tar(args):
    """Check the resources in a tar file, tape, or stdin against the registry.

    Unregistered files are reported but aren't errors. With --archive, also reports
    resources registered to that archive that are missing from the tar file (or not
    checked, if reading stopped), and resources in it that aren't registered there.
    Returns 1 if anything fails, is missing, or can't be read.
    """
    log.info("registry: %s", args.registry_url)
    log.info("source: %s", args.tar)
    counts = Counter()
    unregistered = []
    read_error = False
    seen = set()
    not_located = []
    with util.registry_session(args.registry_url, auth=args.auth) as session:
        expected = {}
        if args.archive is not None:
            url, _ = registry.get_archive(args.registry_url, args.archive)
            if util.query_registry(session, url) is None:
                log.error("error: no archive '%s' in the registry", args.archive)
                return 1
            expected = check.registry_resources_in_archive(
                session, args.registry_url, args.archive
            )
            log.info(
                "archive: %s (%d resources in the registry)",
                args.archive,
                len(expected),
            )
        lookup = common.tar_lookup(session, args.registry_url, unregistered)
        try:
            with transfer.open_tar(args.tar) as tarf, console.Progress() as progress:
                for res in transfer.iter_tar_resources(tarf, lookup):
                    seen.add(res.record["name"])
                    if (
                        args.archive is not None
                        and args.archive not in res.record["locations"]
                    ):
                        log.warning(
                            "  - %s -> not registered to %s", res.name, args.archive
                        )
                        not_located.append(res.record["name"])
                    progress.start(res.name, res.size)
                    try:
                        received = transfer.receive_file(
                            None,
                            res.record["name"],
                            res.name,
                            res.data,
                            res.record["sha1"],
                            progress=progress,
                        )
                    except transfer.TarReadError:
                        log.error("  ✗ %s -> unable to read; stopping", res.name)
                        counts["failed"] += 1
                        raise
                    except transfer.TransferError as err:
                        log.error("  ✗ %s -> %s", res.name, err)
                        counts["failed"] += 1
                        continue
                    if received.verified:
                        note = ""
                        counts["ok"] += 1
                    else:
                        note = " (no registered hash to check)"
                        counts["unverified"] += 1
                    log.info("  - %s -> OK%s  (%s)", res.name, note, progress.summary())
        except (OSError, tarfile.TarError) as err:
            log.error("error: unable to read %s: %s", args.tar, err)
            read_error = True
    unread = sorted(set(expected) - seen)
    # after a read error, resources that weren't reached may still be there
    missing, not_checked = ([], unread) if read_error else (unread, [])
    for name in missing:
        log.error(
            "  ✗ %s: registered to %s but MISSING from the tar file", name, args.archive
        )
    for name in not_checked:
        log.warning("  ? %s: not checked, as reading stopped before it", name)
    log.info(
        "\nResources checked: %d; failed: %d; no registered hash: %d; "
        "files not in the registry: %d",
        counts.total(),
        counts["failed"],
        counts["unverified"],
        len(unregistered),
    )
    if args.archive is not None:
        stopped = (
            f"; not checked (reading stopped): {len(not_checked)}" if read_error else ""
        )
        log.info(
            "Missing from the tar file: %d%s; not registered to %s: %d",
            len(missing),
            stopped,
            args.archive,
            len(not_located),
        )
    return 1 if counts["failed"] or missing or read_error else 0
