# -*- mode: python -*-
"""Tests of nbank.admin, which is run as a script."""

import os
import subprocess
import sys

import pytest

from nbank import admin, core, util
from nbank import archive as nbank_archive


@pytest.fixture
def admin_cli(registry):
    """Returns a function that runs nbank.admin and returns the finished process."""
    user, password = registry.auth

    def run(*args: str, url: str | None = registry.url):
        env = {k: v for k, v in os.environ.items() if k != "NBANK_REGISTRY"}
        cmd = [sys.executable, "-m", "nbank.admin", "-a", f"{user}:{password}"]
        if url is not None:
            cmd += ["-r", url]
        return subprocess.run(
            [*cmd, *args], env=env, capture_output=True, text=True, check=False
        )

    return run


def listing(tmp_path, *names):
    path = tmp_path / "resources.txt"
    path.write_text("# resources\n\n" + "\n".join(names) + "\n")
    return path


def stored_path(archive, name):
    return nbank_archive.resource_path(archive.config, name, resolve_ext=True)


def test_delete(admin_cli, registry, archive, dtype, deposit_file, tmp_path, unique):
    names = [deposit_file(archive, dtype) for _ in range(2)]
    paths = [stored_path(archive, name) for name in names]
    missing = unique("missing")
    result = admin_cli("delete", str(listing(tmp_path, missing, *names)))
    assert result.returncode == 0, result.stderr
    assert "not in the registry, skipping" in result.stderr
    for name, path in zip(names, paths, strict=True):
        assert not path.exists()
        assert core.describe(registry.url, name) is None


def test_delete_dry_run(admin_cli, registry, archive, dtype, deposit_file, tmp_path):
    name = deposit_file(archive, dtype)
    result = admin_cli("delete", "-y", str(listing(tmp_path, name)))
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stderr
    assert stored_path(archive, name).exists()
    assert core.describe(registry.url, name) is not None


def test_delete_resource_without_script_globals(registry, client, archive, register):
    name = register(archive=archive.name)["name"]
    admin.delete_resource(name, session=client, registry_url=registry.url)
    assert core.describe(registry.url, name) is None


def test_update_hash(admin_cli, registry, archive, dtype, deposit_file, tmp_path):
    names = [deposit_file(archive, dtype) for _ in range(2)]
    assert all(core.describe(registry.url, n)["sha1"] is None for n in names)
    result = admin_cli("update-hash", str(listing(tmp_path, *names)))
    assert result.returncode == 0, result.stderr
    assert "[1/2]" in result.stderr
    assert "[2/2]" in result.stderr
    for name in names:
        expected = util.hash(stored_path(archive, name))
        assert core.describe(registry.url, name)["sha1"] == expected


def test_update_hash_dry_run(
    admin_cli, registry, archive, dtype, deposit_file, tmp_path
):
    name = deposit_file(archive, dtype)
    result = admin_cli("update-hash", "-y", str(listing(tmp_path, name)))
    assert result.returncode == 0, result.stderr
    assert "would update hash" in result.stderr
    assert core.describe(registry.url, name)["sha1"] is None


def test_update_hash_skips_resources_not_on_this_host(
    admin_cli, registry, archive, dtype, deposit_file, register, tmp_path
):
    # registered in the archive, but the file is not there
    absent = register(archive=archive.name)["name"]
    present = deposit_file(archive, dtype)
    result = admin_cli("update-hash", str(listing(tmp_path, absent, present)))
    assert result.returncode == 0, result.stderr
    expected = util.hash(stored_path(archive, present))
    assert core.describe(registry.url, present)["sha1"] == expected
    assert core.describe(registry.url, absent)["sha1"] is None


def test_requires_registry_url(admin_cli, tmp_path):
    result = admin_cli("delete", str(listing(tmp_path)), url=None)
    assert result.returncode != 0
    assert "supply a registry url" in result.stderr
