# -*- mode: python -*-
import argparse
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from nbank import archive, script
from nbank.script.parser import userpwd


def test_userpwd():
    assert userpwd("user:secret") == ("user", "secret")


def test_userpwd_password_with_colons():
    assert userpwd("user:pa:ss:word") == ("user", "pa:ss:word")


def test_userpwd_without_colon():
    with pytest.raises(argparse.ArgumentTypeError):
        userpwd("user")


def test_check_archive_command(monkeypatch):
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    received = []

    def fake_check_archive(args):
        received.append(args)
        return 1

    monkeypatch.setattr(script.checks, "check_archive", fake_check_archive)
    # the command is replaced, so it can run on any host
    monkeypatch.setattr(archive, "_posix", True)
    try:
        status = script.main(
            [
                "-r",
                "https://localhost/",
                "check",
                "archive",
                "--fix",
                "--no-hash",
                "archive",
            ]
        )
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)
    assert status == 1
    [args] = received
    assert (args.fix, args.no_hash, args.verbose, args.archive) == (
        True,
        True,
        False,
        "archive",
    )


def parse_value(value):
    return script.parser.ParseKeyVal.parse_value(None, value)


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


@pytest.mark.parametrize("value", ["tags", "a=b=c"])
def test_keyval_without_one_equals_is_a_usage_error(capsys, value):
    p = argparse.ArgumentParser()
    p.add_argument("-m", "--meta", action=script.ParseKeyVal)
    with pytest.raises(SystemExit) as exc:
        p.parse_args(["--meta", value])
    assert exc.value.code == 2
    assert f"--meta {value}: needs key=value" in capsys.readouterr().err


def test_keyval_leaves_the_default_unchanged():
    p = argparse.ArgumentParser()
    p.add_argument("-k", action=script.ParseKeyVal, default={}, dest="metadata")
    assert p.parse_args(["-k", "a=1"]).metadata == {"a": 1}
    assert p.parse_args([]).metadata == {}


def test_search_keeps_underscored_values_as_strings(monkeypatch):
    captured = {}

    def fake_search(url, **params):
        captured.update(params)
        return iter(())

    monkeypatch.setattr(script.resources.core, "search", fake_search)
    script.main(["-r", "https://localhost/", "search", "-k", "name=20140603_1_1"])
    assert captured == {"metadata__name": "20140603_1_1", "superseded": False}


def test_registry_default_is_read_when_run(monkeypatch):
    monkeypatch.setenv("NBANK_REGISTRY", "https://example.org/neurobank/")
    # the parser doesn't hold the value, so documentation built from it can't show it
    assert script.make_parser().parse_args(["registry", "info"]).registry_url is None
    seen = []
    monkeypatch.setattr(
        script.resources, "registry_info", lambda args: seen.append(args)
    )
    script.main(["registry", "info"])
    assert seen[0].registry_url == "https://example.org/neurobank/"


@pytest.mark.parametrize("command", ["locate", "fetch", "export", "copy"])
def test_alt_base_default_is_read_when_run(monkeypatch, command):
    monkeypatch.setenv("NBANK_ALT_BASE", "/mnt/data")
    argv = {"export": [command, "out"], "copy": [command, "dest"]}.get(
        command, [command, "res_1"]
    )
    assert script.make_parser().parse_args(argv).alt_base is None
    seen = []
    func = {
        "locate": "nbank.script.resources.locate_resources",
        "fetch": "nbank.script.resources.fetch_resources",
        "export": "nbank.script.transfers.export_resources",
        "copy": "nbank.script.transfers.copy_resources",
    }[command]
    monkeypatch.setattr(func, lambda args: seen.append(args))
    # the command is replaced, so it can run on any host
    monkeypatch.setattr(archive, "_posix", True)
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    try:
        script.main(["-r", "https://localhost/", *argv])
        script.main(["-r", "https://localhost/", *argv, "--alt-base", "/other"])
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)
    assert [args.alt_base for args in seen] == [Path("/mnt/data"), Path("/other")]


_flood = """
import sys
from nbank import script

def flood(args):
    for i in range(int(sys.argv[1])):
        print(i)

script.resources.search_resources = flood
sys.exit(script.main(["-r", "https://localhost/", "search", "x"]))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="SIGPIPE is POSIX")
@pytest.mark.parametrize(
    "lines, read",
    [(1_000_000, 1), (10, 0)],
    # a long output fails while printing, a short one when it's flushed
    ids=["while printing", "when flushed"],
)
def test_closed_pipe_exits_quietly(lines, read):
    # as `nbank search ... | head` does
    proc = subprocess.Popen(
        [sys.executable, "-c", _flood, str(lines)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for i in range(read):
        assert proc.stdout.readline() == f"{i}\n".encode()
    proc.stdout.close()
    stderr = proc.stderr.read().decode()
    assert proc.wait(timeout=30) == 141
    assert stderr == ""
