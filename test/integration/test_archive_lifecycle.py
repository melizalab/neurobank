# -*- mode: python -*-
"""Tests of the commands that keep an archive and the registry consistent."""

import json
import tarfile

import pytest

from nbank import archive as nbank_archive
from nbank import check, core
from nbank import registry as nbank_registry


def stored_path(archive, name):
    return nbank_archive.resource_path(archive.config, name, resolve_ext=True)


def make_tar(path, *files):
    with tarfile.open(path, "w") as tar:
        for f in files:
            tar.add(f, arcname=f.name)
    return path


def check_summary(caplog, total, missing_archive, missing_registry, errors):
    assert (
        f"Resources in registry: {total}; missing from archive: {missing_archive}; "
        f"missing from registry: {missing_registry}; read/verify errors: {errors}"
    ) in caplog.text


def test_check_consistent(cli, archive, dtype, deposit_file, caplog):
    names = [deposit_file(archive, dtype, hash=True) for _ in range(2)]
    assert cli("check", "archive", "-v", str(archive.path)) == 0
    for name in names:
        assert f" - {name} : {stored_path(archive, name)} - OK" in caplog.text
    check_summary(caplog, 2, 0, 0, 0)
    assert "permission errors: 0" in caplog.text


def test_check_missing_from_archive(cli, register, archive, caplog):
    name = register(archive=archive.name)["name"]
    assert cli("check", "archive", str(archive.path)) == 1
    assert f" - {name}: MISSING from the archive!" in caplog.text
    check_summary(caplog, 1, 1, 0, 0)


def test_check_missing_from_registry(cli, archive, tmp_path, unique, caplog):
    name = unique("res")
    src = tmp_path / f"{name}.txt"
    src.write_text("contents")
    stored = nbank_archive.store_resource(archive.config, src, id=name)
    cli("check", "archive", str(archive.path))
    assert (
        f" - {stored}: MISSING from the registry under {archive.name}!" in caplog.text
    )
    check_summary(caplog, 0, 0, 1, 0)


def test_check_changed_contents(cli, archive, dtype, deposit_file, caplog):
    name = deposit_file(archive, dtype, hash=True)
    stored_path(archive, name).write_text("changed")
    assert cli("check", "archive", str(archive.path)) == 1
    assert "FAILED to match hash!" in caplog.text
    check_summary(caplog, 1, 0, 0, 1)
    caplog.clear()
    assert cli("check", "archive", "--no-hash", "-v", str(archive.path)) == 0
    assert "OK (hash not checked)" in caplog.text
    check_summary(caplog, 1, 0, 0, 0)


def test_check_not_an_archive(cli, tmp_path, caplog):
    assert cli("check", "archive", str(tmp_path)) == 1
    assert "is not a valid neurobank archive" in caplog.text


def test_check_unregistered_archive(cli, registry, tmp_path, caplog):
    config = nbank_archive.create(tmp_path / "unregistered", registry.url)
    assert cli("check", "archive", str(config["path"])) == 1
    assert "No archive associated with" in caplog.text


def test_check_ignores_archive_with_similar_name(
    cli, client, registry, register, archive, tmp_path, caplog
):
    # the registry's location filter matches substrings of archive names
    other = f"{archive.name}-copy"
    url, body = nbank_registry.add_archive(
        registry.url, other, "neurobank", tmp_path / other
    )
    client.post(url, json=body).raise_for_status()
    register(archive=other)
    cli("check", "archive", str(archive.path))
    assert "MISSING from the archive" not in caplog.text
    check_summary(caplog, 0, 0, 0, 0)


def test_check_fix_permissions(cli, archive, dtype, deposit_file, caplog):
    name = deposit_file(archive, dtype, hash=True)
    path = stored_path(archive, name)
    path.chmod(0o600)
    path.parent.chmod(0o700)
    assert cli("check", "archive", str(archive.path)) == 1
    assert "permission errors: 2" in caplog.text
    caplog.clear()
    assert cli("check", "archive", "--fix", str(archive.path)) == 0
    assert "permission errors: 0" in caplog.text
    assert "Permission errors fixed: 2" in caplog.text
    caplog.clear()
    assert cli("check", "archive", str(archive.path)) == 0
    assert "permission errors: 0" in caplog.text


def test_check_unknown_archive_user(cli, archive, caplog):
    config_file = archive.path / "nbank.json"
    config = json.loads(config_file.read_text())
    config["policy"]["access"]["user"] = "no-such-user-xyzzy"
    config_file.write_text(json.dumps(config))
    assert cli("check", "archive", str(archive.path)) == 1
    assert "unable to check: archive user 'no-such-user-xyzzy'" in caplog.text


@pytest.fixture
def has_location_filter(client, registry, register, archive):
    """True if the registry supports the has_location filter."""
    name = register(archive=archive.name)["name"]
    url, params = nbank_registry.find_resource(
        registry.url, name=name, has_location="false"
    )
    r = client.get(url, params=params)
    r.raise_for_status()
    return len(r.json()) == 0


def test_check_registry(cli, register, make_archive, has_location_filter, caplog):
    if not has_location_filter:
        pytest.skip("registry doesn't support the has_location filter")
    full = make_archive()
    empty = make_archive()
    orphan = register()["name"]
    placed = register(archive=full.name)["name"]
    assert cli("check", "registry") == 1
    assert f" - {orphan}: has NO locations!" in caplog.text
    assert placed not in caplog.text
    assert f" - archive {empty.name}: has no resources" in caplog.text
    assert f" - archive {full.name}:" not in caplog.text


def test_check_registry_similar_archive_names(
    cli, client, registry, register, make_archive, has_location_filter, caplog
):
    if not has_location_filter:
        pytest.skip("registry doesn't support the has_location filter")
    empty = make_archive()
    # the registry's location filter matches this archive's resources too
    other = f"{empty.name}-copy"
    url, body = nbank_registry.add_archive(
        registry.url, other, "neurobank", f"/nonexistent/{other}"
    )
    client.post(url, json=body).raise_for_status()
    register(archive=other)
    cli("check", "registry")
    assert f" - archive {empty.name}: has no resources" in caplog.text
    assert f" - archive {other}:" not in caplog.text


def test_check_registry_unsupported(cli, has_location_filter, caplog):
    if has_location_filter:
        pytest.skip("registry supports the has_location filter")
    assert cli("check", "registry") == 1
    assert "doesn't support the has_location filter" in caplog.text


def failed_checks(caplog):
    """Returns the names in check all's list of failed checks."""
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("failed checks: "):
            return message.removeprefix("failed checks: ").split(", ")
    return []


def test_check_all(
    cli,
    client,
    registry,
    register,
    make_archive,
    dtype,
    deposit_file,
    unique,
    tmp_path,
    caplog,
):
    def add_archive(name, scheme, root):
        url, body = nbank_registry.add_archive(registry.url, name, scheme, root)
        client.post(url, json=body).raise_for_status()

    good = make_archive()
    deposit_file(good, dtype, hash=True)
    bad = make_archive()
    register(archive=bad.name)
    unmounted = unique("arch")
    add_archive(unmounted, "neurobank", f"/nonexistent/{unmounted}")
    tape = unique("tape")
    add_archive(tape, "tape", f"{tape}:1")
    linked = unique("arch")
    real = nbank_archive.create(tmp_path / f"{linked}-real", registry.url)
    (tmp_path / linked).symlink_to(real["path"])
    add_archive(linked, "neurobank", tmp_path / linked)
    elsewhere = unique("arch")
    config = nbank_archive.create(tmp_path / elsewhere, "https://elsewhere/")
    add_archive(elsewhere, "neurobank", config["path"])

    assert cli("check", "all") == 1
    failed = failed_checks(caplog)
    assert good.name not in failed
    assert f"archive {good.name}: {good.path}" in caplog.text
    assert bad.name in failed
    assert (
        f" - {unmounted}: /nonexistent/{unmounted} is not on this host" in caplog.text
    )
    assert f" - {tape}: tape archive" in caplog.text
    assert linked in failed
    assert "deposits won't find the archive" in caplog.text
    assert elsewhere in failed
    assert (
        "nbank.json points to a different registry (https://elsewhere/)" in caplog.text
    )


def test_check_all_continues_after_crash(
    cli, make_archive, dtype, deposit_file, monkeypatch, caplog
):
    broken = make_archive()
    deposit_file(broken, dtype)
    original = check.check_archive_contents

    def crash_on_broken(archive_path, *args, **kwargs):
        if archive_path == broken.path:
            raise OSError("simulated failure")
        return original(archive_path, *args, **kwargs)

    monkeypatch.setattr(check, "check_archive_contents", crash_on_broken)
    assert cli("check", "all") == 1
    assert " - unable to check: simulated failure!" in caplog.text
    assert broken.name in failed_checks(caplog)


def test_check_resource_without_hash(cli, archive, dtype, deposit_file, caplog):
    deposit_file(archive, dtype)
    cli("check", "archive", "-v", str(archive.path))
    assert "OK (no hash to verify)" in caplog.text
    check_summary(caplog, 1, 0, 0, 0)


@pytest.fixture
def two_archives(archive, make_archive):
    return archive, make_archive(require_hash=False)


def listing(tmp_path, *names):
    path = tmp_path / "prune.txt"
    path.write_text("# resources to prune\n\n" + "\n".join(names) + "\n")
    return path


def test_prune(cli, registry, two_archives, dtype, deposit_file, replicate, tmp_path):
    a, b = two_archives
    name = deposit_file(a, dtype)
    replicate(name, a, b)
    path_a = stored_path(a, name)
    cli("archive", "prune", a.name, str(listing(tmp_path, name)))
    assert not path_a.exists()
    assert stored_path(b, name).exists()
    assert core.describe(registry.url, name)["locations"] == [b.name]


def test_prune_dry_run(
    cli, registry, two_archives, dtype, deposit_file, replicate, tmp_path
):
    a, b = two_archives
    name = deposit_file(a, dtype)
    replicate(name, a, b)
    cli("archive", "prune", "-y", a.name, str(listing(tmp_path, name)))
    assert stored_path(a, name).exists()
    assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}


def test_prune_keeps_only_copy(
    cli, registry, two_archives, dtype, deposit_file, tmp_path, caplog
):
    a, _ = two_archives
    name = deposit_file(a, dtype)
    cli("archive", "prune", a.name, str(listing(tmp_path, name)))
    assert "this archive is the only location" in caplog.text
    assert stored_path(a, name).exists()
    assert core.describe(registry.url, name)["locations"] == [a.name]


def test_prune_resource_elsewhere(
    cli, two_archives, dtype, deposit_file, tmp_path, caplog
):
    a, b = two_archives
    name = deposit_file(b, dtype)
    cli("archive", "prune", a.name, str(listing(tmp_path, name)))
    assert "not in this archive" in caplog.text
    assert stored_path(b, name).exists()


def test_prune_unknown_resource_and_archive(cli, archive, tmp_path, unique, caplog):
    missing = unique("missing")
    cli("archive", "prune", archive.name, str(listing(tmp_path, missing)))
    assert f"{missing}: not in registry" in caplog.text
    cli("archive", "prune", unique("arch"), str(listing(tmp_path, missing)))
    assert "No such archive" in caplog.text


def test_register_tar(
    cli, registry, client, archive, dtype, deposit_file, tmp_path, unique, caplog
):
    name = deposit_file(archive, dtype)
    other = tmp_path / f"{unique('other')}.txt"
    other.write_text("not in the registry")
    tar = make_tar(tmp_path / "archive.tar", stored_path(archive, name), other)
    tape = unique("tape")
    cli("archive", "register-tar", "-n", tape, "tape01", "3", str(tar))
    assert set(core.describe(registry.url, name)["locations"]) == {archive.name, tape}
    record = client.get(f"{registry.url}archives/{tape}/").json()
    assert record["scheme"] == "tape"
    assert record["root"] == "tape01:3"
    assert record["accessibility"] == "offline"
    assert f"{other.name} -> no match in registry" in caplog.text


def test_register_tar_dry_run(
    cli, registry, client, archive, dtype, deposit_file, tmp_path, unique
):
    name = deposit_file(archive, dtype)
    tar = make_tar(tmp_path / "archive.tar", stored_path(archive, name))
    tape = unique("tape")
    cli("archive", "register-tar", "-y", "-n", tape, "tape01", "3", str(tar))
    assert core.describe(registry.url, name)["locations"] == [archive.name]
    url = f"{registry.url}archives/{tape}/"
    assert client.get(url).status_code == 404


def test_import_tar(
    cli, registry, two_archives, dtype, deposit_file, tmp_path, unique, caplog
):
    a, b = two_archives
    name = deposit_file(a, dtype, contents="the contents")
    unregistered = tmp_path / f"{unique('other')}.txt"
    unregistered.write_text("not in the registry")
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name), unregistered)

    cli("archive", "import-tar", str(tar), str(b.path))
    assert stored_path(b, name).read_text() == "the contents"
    assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}
    assert "not in the registry" in caplog.text

    caplog.clear()
    cli("archive", "import-tar", str(tar), str(b.path))
    assert "is already in the destination archive" in caplog.text


def test_import_tar_dry_run(cli, registry, two_archives, dtype, deposit_file, tmp_path):
    a, b = two_archives
    name = deposit_file(a, dtype)
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name))
    cli("archive", "import-tar", "-y", str(tar), str(b.path))
    assert core.describe(registry.url, name)["locations"] == [a.name]
    with pytest.raises(FileNotFoundError):
        stored_path(b, name)


def test_import_tar_refused(
    cli, two_archives, dtype, deposit_file, tmp_path, monkeypatch, caplog
):
    a, b = two_archives
    name = deposit_file(a, dtype)
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name))
    # without credentials the registry can be read but not changed
    home = tmp_path / "no_netrc"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cli("archive", "import-tar", str(tar), str(b.path))
    assert "unable to add location" in caplog.text
    assert "Authentication credentials were not provided." in caplog.text
    with pytest.raises(FileNotFoundError):
        stored_path(b, name)


def test_import_tar_file_already_there(
    cli, registry, two_archives, dtype, deposit_file, replicate, tmp_path, caplog
):
    a, b = two_archives
    name = deposit_file(a, dtype)
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name))
    copy = tmp_path / "copy.txt"
    copy.write_text(name)
    nbank_archive.store_resource(b.config, copy, id=name)
    cli("archive", "import-tar", str(tar), str(b.path))
    assert "file is already there but not in registry" in caplog.text
