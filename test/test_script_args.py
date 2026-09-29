# -*- mode: python -*-
import argparse
import logging
from pathlib import Path

import pytest

from nbank import script
from nbank.script import userpwd


def test_userpwd():
    assert userpwd("user:secret") == ("user", "secret")


def test_userpwd_password_with_colons():
    assert userpwd("user:pa:ss:word") == ("user", "pa:ss:word")


def test_userpwd_without_colon():
    with pytest.raises(argparse.ArgumentTypeError):
        userpwd("user")


@pytest.mark.parametrize(
    "command", [["check", "archive"], ["archive", "check"]], ids=["main", "alias"]
)
def test_check_archive_commands(monkeypatch, command):
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    received = []

    def fake_check_archive(args):
        received.append(args)
        return 1

    monkeypatch.setattr(script, "check_archive", fake_check_archive)
    try:
        status = script.main(
            ["-r", "https://localhost/", *command, "--fix", "--no-hash", "archive"]
        )
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)
    assert status == 1
    [args] = received
    assert (args.fix, args.no_hash, args.verbose, args.path) == (
        True,
        True,
        False,
        Path("archive"),
    )
