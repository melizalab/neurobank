# -*- mode: python -*-
"""Tests that locations in archives of a scheme this client doesn't know are skipped,
as a client must when the registry has archives added by a later version."""

import pytest

from nbank import archive as nbank_archive
from nbank import core, util
from nbank import registry as reg

SCHEME = "dataverse"


@pytest.fixture
def unknown_archive(client, registry, unique):
    """The name of an archive with an unknown scheme. Its accessibility is local, and
    the registry lists local archives first, so its locations come first."""
    name = unique("dv")
    url, body = reg.add_archive(
        registry.url, name, SCHEME, f"dataverse.example.org/doi:10.5072/FK2/{name}"
    )
    client.post(url, json=body).raise_for_status()
    return name


@pytest.fixture
def local(make_archive):
    """A neurobank archive on this host, listed after the unknown one."""
    return make_archive(accessibility="remote")


@pytest.fixture
def in_both(client, registry, local, unknown_archive, dtype, deposit_file):
    """The id of a resource in the local archive and the unknown one."""
    name = deposit_file(local, dtype)
    url, body = reg.add_location(registry.url, name, unknown_archive, key="1234")
    client.post(url, json=body).raise_for_status()
    url, params = reg.get_locations(registry.url, name)
    locations = util.query_registry(client, url, params)
    assert [loc["archive_name"] for loc in locations][:2] == [
        unknown_archive,
        local.name,
    ]
    return name


@pytest.fixture
def only_unknown(register, unknown_archive):
    """The id of a resource only in the unknown archive."""
    return register(archive=unknown_archive, key="1235")["name"]


def stored_path(archive, name):
    return nbank_archive.resource_path(archive.config, name, resolve_ext=True)


def test_find_resources(registry, local, in_both, only_unknown):
    results = dict(core.find_resources(registry.url, in_both, only_unknown))
    assert results[in_both] == stored_path(local, in_both)
    assert isinstance(results[only_unknown], FileNotFoundError)


def test_locate(cli, local, in_both, only_unknown, capsys):
    assert cli("locate", in_both) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [f"{in_both:<20}\t{stored_path(local, in_both)}"]
    assert cli("locate", only_unknown) == 1
    assert capsys.readouterr().out == ""


def test_fetch(cli, in_both, only_unknown, tmp_path, capsys):
    dest = tmp_path / "fetched"
    dest.mkdir()
    assert cli("fetch", "-d", str(dest), in_both, only_unknown) == 1
    out = capsys.readouterr().out
    assert f"{in_both:<20}\t-> {dest / in_both}" in out
    assert f"{only_unknown:<20}\t-> (no valid locations)" in out
    assert (dest / in_both).read_text() == in_both
    assert [p.name for p in dest.iterdir()] == [in_both]


def test_copy(cli, registry, make_archive, local, unknown_archive, in_both, caplog):
    dest = make_archive()
    assert cli("copy", str(dest.path), in_both) == 0
    assert "copied: 1" in caplog.text
    assert stored_path(dest, in_both).read_text() == in_both
    assert set(core.describe(registry.url, in_both)["locations"]) == {
        unknown_archive,
        local.name,
        dest.name,
    }


def test_export(cli, in_both, only_unknown, tmp_path, caplog):
    out = tmp_path / "export"
    assert cli("export", str(out), in_both, only_unknown) == 1
    assert f"{only_unknown} -> no copy on this host" in caplog.text
    assert [p.read_text() for p in out.iterdir()] == [in_both]


def test_check(cli, local, unknown_archive, in_both, only_unknown, caplog):
    assert cli("check", "archive", str(local.path)) == 0
    assert cli("check", "archive", unknown_archive) == 1
    assert f"checking {SCHEME} archives isn't supported" in caplog.text
    caplog.clear()
    # other tests leave archives that fail, so only this test's archives are checked
    cli("check", "all")
    assert f" - {unknown_archive}: {SCHEME} archive" in caplog.text
    assert f"archive {local.name}: {local.path}" in caplog.text
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("failed checks: "):
            failed = message.removeprefix("failed checks: ").split(", ")
            assert unknown_archive not in failed
            assert local.name not in failed
