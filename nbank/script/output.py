# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Setting up the console and the log for the nbank command."""

import codecs
import io
import logging
import os
import sys

log = logging.getLogger("nbank")


def utf8_console():
    """Writes standard output and error as UTF-8 when they use another encoding.

    Windows writes redirected output in its ANSI code page, which can't hold every
    name or note in the registry; characters a stream can't take are replaced.
    """
    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue
        if codecs.lookup(stream.encoding).name != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def setup_log(log, debug=False):
    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    loglevel = logging.DEBUG if debug else logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)
    ch.setFormatter(formatter)
    log.addHandler(ch)


def discard_stdout() -> None:
    """Points standard output at the null device, so that flushing it at exit doesn't
    fail again."""
    try:
        fd = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, fd)
    os.close(devnull)
