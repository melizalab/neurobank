# -*- mode: python -*-
"""Tests of the commands that keep an archive and the registry consistent."""

import tarfile

import pytest

from nbank import archive as nbank_archive
from nbank import core


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
    cli("archive", "check", "-v", str(archive.path))
    for name in names:
        assert f" - {name} : {stored_path(archive, name)} - OK" in caplog.text
    check_summary(caplog, 2, 0, 0, 0)


def test_check_missing_from_archive(cli, register, archive, caplog):
    name = register(archive=archive.name)["name"]
    cli("archive", "check", str(archive.path))
    assert f" - {name}: MISSING from the archive!" in caplog.text
    check_summary(caplog, 1, 1, 0, 0)


def test_check_missing_from_registry(cli, archive, tmp_path, unique, caplog):
    name = unique("res")
    src = tmp_path / f"{name}.txt"
    src.write_text("contents")
    stored = nbank_archive.store_resource(archive.config, src, id=name)
    cli("archive", "check", str(archive.path))
    assert (
        f" - {stored}: MISSING from the registry under {archive.name}!" in caplog.text
    )
    check_summary(caplog, 0, 0, 1, 0)


def test_check_changed_contents(cli, archive, dtype, deposit_file, caplog):
    name = deposit_file(archive, dtype, hash=True)
    stored_path(archive, name).write_text("changed")
    cli("archive", "check", str(archive.path))
    assert "FAILED to match hash!" in caplog.text
    check_summary(caplog, 1, 0, 0, 1)


def test_check_not_an_archive(cli, tmp_path, caplog):
    cli("archive", "check", str(tmp_path))
    assert "is not a valid neurobank archive" in caplog.text


def test_check_unregistered_archive(cli, registry, tmp_path, caplog):
    config = nbank_archive.create(tmp_path / "unregistered", registry.url)
    cli("archive", "check", str(config["path"]))
    assert "No archive associated with" in caplog.text


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
