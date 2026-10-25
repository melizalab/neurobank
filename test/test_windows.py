# -*- mode: python -*-
"""Tests of nbank as a Windows client: it imports without POSIX-only modules, and
refuses to write or check archives. Windows is simulated by archive._posix."""

import io
import subprocess
import sys

import pytest

from nbank import archive, check, core, transfer, util
from test.test_nbank import run_main
from test.test_registry import base_url


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(archive, "_posix", False)


refused = "archives are written and checked on a POSIX host"


def test_imports_without_posix_modules():
    code = (
        "import sys\n"
        "sys.modules['grp'] = sys.modules['pwd'] = None\n"
        "import nbank.admin, nbank.check, nbank.core, nbank.script, nbank.transfer\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_create_refused(windows, tmp_path):
    with pytest.raises(archive.ArchiveHostError, match="unable to create an archive"):
        archive.create(tmp_path / "archive", base_url)
    assert not (tmp_path / "archive").exists()


@pytest.fixture
def stored(tmp_archive, tmp_path):
    src = tmp_path / "res_1.txt"
    src.write_text("contents")
    return archive.store_resource(tmp_archive, src)


def test_store_refused(tmp_archive, tmp_path, windows):
    src = tmp_path / "res_2.txt"
    src.write_text("contents")
    with pytest.raises(archive.ArchiveHostError, match="store a resource"):
        archive.store_resource(tmp_archive, src)
    assert src.exists()


def test_remove_refused(stored, windows):
    resource = archive.Resource(stored.parent.parent.parent, "res_1", stored.name)
    assert not resource.deletable
    with pytest.raises(archive.ArchiveHostError, match="remove a file"):
        resource.unlink()
    assert stored.exists()


def test_reading_works(stored, windows, tmp_path):
    resource = archive.Resource(stored.parent.parent.parent, "res_1", stored.name)
    assert resource.fetch(tmp_path).read_text() == "contents"


def test_receive_refused(tmp_archive, windows):
    with pytest.raises(archive.ArchiveHostError):
        transfer.receive_file(tmp_archive, "res_1", "f.wav", io.BytesIO(b"x"), None)
    # a dry run only hashes
    received = transfer.receive_file(None, "res_1", "f.wav", io.BytesIO(b"x"), None)
    assert received.sha1 == util.hash_stream(io.BytesIO(b"x"))


def test_checks_refused(tmp_archive, windows):
    with pytest.raises(archive.ArchiveHostError, match="check an archive"):
        list(check.check_archive_contents(tmp_archive["path"], {}))
    with pytest.raises(archive.ArchiveHostError, match="check an archive"):
        list(check.check_archive_permissions(tmp_archive))


def test_deposit_refused_before_the_registry(
    mocked_api, tmp_archive, tmp_path, windows
):
    src = tmp_path / "res_1.txt"
    src.write_text("contents")
    # no requests are mocked: deposit mustn't make any
    with pytest.raises(archive.ArchiveHostError, match="unable to deposit"):
        list(core.deposit(tmp_archive["path"], [src]))
    assert src.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["init", "archive"],
        ["deposit", "archive", "file"],
        ["copy", "archive", "res_1"],
        ["archive", "import-tar", "file.tar", "archive"],
        ["archive", "prune", "archive", "list"],
        ["archive", "register-tar", "tape01", "1", "file.tar"],
        ["archive", "check", "archive"],
        ["check", "archive", "archive"],
        ["check", "all"],
    ],
    ids=lambda argv: " ".join(argv[:2]),
)
def test_archive_commands_refused(mocked_api, windows, caplog, argv):
    # no requests are mocked: the command mustn't make any
    assert run_main(*argv) == 1
    assert refused in caplog.text


def test_read_commands_run(mocked_api, windows):
    from test.test_registry import resource_url

    mocked_api.get(resource_url).respond(json=[])
    assert run_main("search", "-n", "archive") is None


@pytest.mark.parametrize("command", ["delete", "update-hash"])
def test_admin_file_commands_refused(command, tmp_path):
    code = (
        "import runpy, sys\n"
        "from nbank import archive\n"
        "archive._posix = False\n"
        f"sys.argv = ['nbank.admin', '-r', '{base_url}', '{command}', 'res_1']\n"
        "runpy.run_module('nbank.admin', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 1
    assert refused in result.stderr


def test_console_written_as_utf8():
    # a redirected Windows console writes in its ANSI code page
    code = (
        "import io, sys\n"
        "from nbank import script\n"
        "sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')\n"
        "script.utf8_console()\n"
        "print('Zebrafinch \\u2192 \\u5c0f')\n"
        "sys.stdout.flush()\n"
        "sys.__stdout__.write(sys.stdout.buffer.getvalue().decode('utf-8'))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "Zebrafinch \u2192 \u5c0f\n"
