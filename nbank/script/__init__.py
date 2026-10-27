# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""The nbank command line: main() runs it, and make_parser() builds its parser."""

import datetime
import logging
import os
import sys
from netrc import NetrcParseError
from pathlib import Path

import httpx

from nbank import __version__, archive, core, registry, util
from nbank.script import output, parser
from nbank.script.parser import ParseKeyVal, make_parser

log = logging.getLogger("nbank")


def main(argv=None) -> int | None:
    """Runs the nbank command line with argv (default: sys.argv[1:]) and returns
    the exit status (None for success)."""
    p = parser.make_parser()
    args = p.parse_args(argv)

    if not hasattr(args, "func"):
        p.print_usage()
        return 0

    output.utf8_console()
    output.setup_log(log, args.debug)
    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())

    # read after parsing, so the parser (and documentation built from it) doesn't
    # hold this host's value. Checks of an archive prefer -r to its nbank.json, but
    # not NBANK_REGISTRY
    args.registry_given = args.registry_url is not None
    if args.registry_url is None:
        args.registry_url = registry.default_registry()
    if getattr(args, "alt_base", False) is None and os.environ.get(parser.env_alt_base):
        args.alt_base = Path(os.environ[parser.env_alt_base])
    # resolved after parsing so that a missing ~/.netrc doesn't break the parser
    try:
        args.auth = core.make_auth(args.auth)
    except NetrcParseError as err:
        log.error("error: unable to use netrc file: %s", err.msg)
        return 1

    if getattr(args, "rename", False) and not args.fix:
        log.error("error: --rename needs --fix")
        return 1

    if not archive._posix and getattr(args, "posix_archive", False):
        log.error(
            "error: archives are written and checked on a POSIX host (Linux or "
            "macOS), not on Windows"
        )
        return 1

    # most commands require a registry, so check it here once; some can use the one
    # in an archive's nbank.json
    if args.registry_url is None and not getattr(args, "registry_in_archive", False):
        log.error(
            "error: supply a registry url with '-r' or %s environment variable",
            registry._env_registry,
        )
        return 1

    # some of the error handling is common; sub-funcs should only catch specific
    # errors. Commands return 1 (or None for success) and exit status follows.
    try:
        status = args.func(args)
        # buffered output reaches a closed pipe here
        sys.stdout.flush()
        return status
    except BrokenPipeError:
        # the reader stopped early (e.g. `| head`): exit as if killed by SIGPIPE
        output.discard_stdout()
        return 141
    except httpx.RequestError:
        log.error("registry error: unable to contact server")
    except util.RegistryVersionError as err:
        log.error("error: %s", err)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            log.error(
                "authentication error: Authenticate with '-a username:password' or .netrc file."
            )
            log.error(
                "                      Or, you may not have permission for this operation."
            )
        elif e.response.is_server_error:
            log.error(
                "registry error: %s %s",
                e.response.status_code,
                e.response.reason_phrase,
            )
        else:
            registry.log_error(e)
    except KeyboardInterrupt:
        return 130
    return 1


# ParseKeyVal is used by other packages' scripts (melizalab-tools)
__all__ = ["ParseKeyVal", "main", "make_parser"]
