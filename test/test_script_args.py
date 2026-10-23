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


def parse_value(value):
    return script.ParseKeyVal.parse_value(None, value)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("33", 33),
        ("-1.5e-3", -0.0015),
        ("[1, 2]", [1, 2]),
        ('["a", "b"]', ["a", "b"]),
        ('{"rate": 44100, "tags": ["x"]}', {"rate": 44100, "tags": ["x"]}),
        ("true", True),
        ("null", None),
        ('"20140603"', "20140603"),
        ("chorus", "chorus"),
        ("", ""),
        # aliases for values written as Python literals
        ("True", True),
        ("False", False),
        ("None", None),
    ],
)
def test_parse_value_reads_json(value, expected):
    parsed = parse_value(value)
    assert parsed == expected
    assert type(parsed) is type(expected)


@pytest.mark.parametrize(
    "value",
    [
        # Python reads all of these as numbers, but they aren't JSON
        "20140603_1_1",
        "1_000",
        "0x1f",
        "0o17",
        "1j",
        "1.",
        ".5",
        "+2",
        "007",
        # JSON constants Python's json accepts but the registry can't store
        "NaN",
        "Infinity",
        "-Infinity",
        # not JSON
        "2016-05-26",
        "'quoted'",
        "true story",
    ],
)
def test_parse_value_keeps_other_values_as_strings(value):
    assert parse_value(value) == value


@pytest.mark.parametrize(
    "value", ["[1, 2", "['a', 'b']", "[True]", "[NaN]", '{"a": 1,}', " {a: 1}"]
)
def test_parse_value_rejects_malformed_lists_and_objects(value):
    with pytest.raises(ValueError, match="not a valid JSON list or object"):
        parse_value(value)


def test_malformed_list_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        script.main(["-r", "https://localhost/", "search", "-k", "tags=[1, 2"])
    assert exc.value.code == 2
    assert (
        "-k tags: [1, 2 is not a valid JSON list or object" in capsys.readouterr().err
    )


def test_search_keeps_underscored_values_as_strings(monkeypatch):
    captured = {}

    def fake_search(url, **params):
        captured.update(params)
        return iter(())

    monkeypatch.setattr(script.core, "search", fake_search)
    script.main(["-r", "https://localhost/", "search", "-k", "name=20140603_1_1"])
    assert captured == {"metadata__name": "20140603_1_1", "superseded": False}
