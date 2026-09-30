# -*- mode: python -*-
"""Tests of the commands that keep an archive and the registry consistent."""

import json
import re
import shutil
import tarfile
from types import SimpleNamespace

import pytest

from nbank import archive as nbank_archive
from nbank import check, core, script, transfer, util
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
    assert f" - {name}: MISSING from the archive" in caplog.text
    check_summary(caplog, 1, 1, 0, 0)


def test_check_missing_from_registry(cli, archive, tmp_path, unique, caplog):
    name = unique("res")
    src = tmp_path / f"{name}.txt"
    src.write_text("contents")
    stored = nbank_archive.store_resource(archive.config, src, id=name)
    cli("check", "archive", str(archive.path))
    assert f" - {stored}: MISSING from the registry under {archive.name}" in caplog.text
    check_summary(caplog, 0, 0, 1, 0)


def test_check_changed_contents(cli, archive, dtype, deposit_file, caplog):
    name = deposit_file(archive, dtype, hash=True)
    path = stored_path(archive, name)
    path.chmod(0o644)
    path.write_text("changed")
    path.chmod(0o444)
    assert cli("check", "archive", str(archive.path)) == 1
    assert "FAILED to match hash" in caplog.text
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


def test_check_registry(cli, register, make_archive, caplog):
    full = make_archive()
    empty = make_archive()
    orphan = register()["name"]
    placed = register(archive=full.name)["name"]
    assert cli("check", "registry") == 1
    assert f" - {orphan}: has NO locations" in caplog.text
    assert placed not in caplog.text
    assert f" - archive {empty.name}: has no resources" in caplog.text
    assert f" - archive {full.name}:" not in caplog.text


def test_check_registry_similar_archive_names(
    cli, client, registry, register, make_archive, caplog
):
    empty = make_archive()
    # a substring match on the archive name would find this archive's resources
    other = f"{empty.name}-copy"
    url, body = nbank_registry.add_archive(
        registry.url, other, "neurobank", f"/nonexistent/{other}"
    )
    client.post(url, json=body).raise_for_status()
    register(archive=other)
    cli("check", "registry")
    assert f" - archive {empty.name}: has no resources" in caplog.text
    assert f" - archive {other}:" not in caplog.text


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
    assert " - unable to check: simulated failure" in caplog.text
    assert broken.name in failed_checks(caplog)


def stray_copy(name, src, dst, tmp_path):
    """Copies a resource into another archive without telling the registry."""
    stored = stored_path(src, name)
    tmp = tmp_path / stored.name
    shutil.copy(stored, tmp)
    return nbank_archive.store_resource(dst.config, tmp, id=name)


@pytest.fixture
def answers(monkeypatch):
    """Returns a function that scripts answers to the check's prompts.

    The function returns the list of prompts shown. Running out of answers
    acts like end of input.
    """

    def set_answers(*replies):
        prompts = []
        remaining = iter(replies)

        def fake_input(prompt):
            prompts.append(prompt)
            try:
                return next(remaining)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr(script, "_interactive", lambda: True)
        monkeypatch.setattr("builtins.input", fake_input)
        return prompts

    return set_answers


@pytest.fixture
def copied(archive, make_archive, dtype, deposit_file, tmp_path):
    """A resource deposited in one archive, with an unregistered copy in another.

    Has the resource's name, the original archive, the archive with the copy,
    and the path of the copy.
    """
    original = make_archive()
    name = deposit_file(original, dtype, hash=True)
    path = stray_copy(name, original, archive, tmp_path)
    return SimpleNamespace(name=name, original=original, archive=archive, path=path)


def test_check_registered_elsewhere(cli, copied, caplog):
    assert cli("check", "archive", str(copied.archive.path)) == 1
    assert (
        f" - {copied.path}: registered, but NOT located in this archive "
        f"(located in: {copied.original.name})"
    ) in caplog.text
    assert "MISSING from the registry" not in caplog.text
    assert "registered elsewhere: 1" in caplog.text


def test_check_fix_adds_location(cli, registry, copied, answers, caplog):
    prompts = answers("a")
    assert cli("check", "archive", "--fix", str(copied.archive.path)) == 0
    assert "[d]elete this copy" in prompts[0]
    assert "Files registered elsewhere resolved: 1" in caplog.text
    locations = core.describe(registry.url, copied.name)["locations"]
    assert sorted(locations) == sorted([copied.original.name, copied.archive.name])
    assert cli("check", "archive", str(copied.archive.path)) == 0


def test_check_fix_deletes_copy(cli, registry, copied, answers):
    answers("d")
    assert cli("check", "archive", "--fix", str(copied.archive.path)) == 0
    assert not copied.path.exists()
    assert core.describe(registry.url, copied.name)["locations"] == [
        copied.original.name
    ]


def test_check_fix_changed_copy(cli, copied, answers, caplog):
    # a different file with the same name as the resource isn't a copy
    copied.path.chmod(0o644)
    copied.path.write_text("changed")
    prompts = answers("d")
    assert cli("check", "archive", "--fix", str(copied.archive.path)) == 1
    assert "contents DIFFER from the registered hash" in caplog.text
    assert prompts == []
    assert copied.path.exists()


def test_check_fix_unverified_copy(
    cli, registry, archive, make_archive, dtype, deposit_file, answers, tmp_path, caplog
):
    original = make_archive(require_hash=False)
    name = deposit_file(original, dtype)
    path = stray_copy(name, original, archive, tmp_path)
    prompts = answers("d")
    assert cli("check", "archive", "--fix", str(archive.path)) == 0
    assert "no hash to verify contents" in caplog.text
    assert f"the registry has no hash for {name}" in caplog.text
    assert "[d]elete this copy" in prompts[0]
    assert "[a]dd" in prompts[0]
    assert not path.exists()


def test_check_fix_misplaced_copy(cli, copied, answers):
    wrong_dir = copied.archive.path / "resources" / "zz"
    wrong_dir.mkdir()
    moved = copied.path.rename(wrong_dir / copied.path.name)
    prompts = answers("s")
    assert cli("check", "archive", "--fix", str(copied.archive.path)) == 1
    # adding the location would register a file that can't be found
    assert "[a]dd" not in prompts[0]
    assert moved.exists()


def test_check_fix_only_copy(
    cli, registry, register, archive, answers, tmp_path, caplog
):
    # registered with no locations, so this file is the only copy
    name = register()["name"]
    src = tmp_path / f"{name}.txt"
    src.write_text("contents")
    path = nbank_archive.store_resource(archive.config, src, id=name)
    prompts = answers("d", "a")
    assert cli("check", "archive", "--fix", str(archive.path)) == 0
    assert f"the registry has no hash for {name}" in caplog.text
    assert "[d]elete" not in prompts[0]
    assert path.exists()
    assert core.describe(registry.url, name)["locations"] == [archive.name]


def test_check_fix_not_interactive(cli, copied, monkeypatch, caplog):
    def no_input(prompt):
        raise AssertionError("should not prompt")

    monkeypatch.setattr(script, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", no_input)
    assert cli("check", "archive", "--fix", str(copied.archive.path)) == 1
    assert "not an interactive terminal" in caplog.text
    assert copied.path.exists()


@pytest.fixture
def leftover(archive):
    """A file left over from an unfinished transfer into archive."""
    stub = archive.path / "resources" / "re"
    stub.mkdir(exist_ok=True)
    nbank_archive.permission_fixer(archive.config)(stub)
    path = stub / transfer._partial_name("res_1.wav")
    path.write_text("half a file")
    return path


def test_check_incomplete_transfer(cli, archive, leftover, caplog):
    assert cli("check", "archive", str(archive.path)) == 1
    assert f" - {leftover} - INCOMPLETE transfer (safe to delete)" in caplog.text
    assert "permission errors: 0" in caplog.text
    assert "MISSING from the registry" not in caplog.text


@pytest.mark.parametrize("answer, deleted", [("d", True), ("s", False)])
def test_check_fix_incomplete_transfer(
    cli, archive, leftover, answers, answer, deleted
):
    prompts = answers(answer)
    status = cli("check", "archive", "--fix", str(archive.path))
    assert status == (0 if deleted else 1)
    assert prompts == ["   [d]elete, [s]kip? "]
    assert leftover.exists() != deleted


def test_check_fix_incomplete_transfer_not_interactive(
    cli, archive, leftover, monkeypatch
):
    monkeypatch.setattr(script, "_interactive", lambda: False)
    assert cli("check", "archive", "--fix", str(archive.path)) == 1
    assert leftover.exists()


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
    assert cli("archive", "prune", a.name, str(listing(tmp_path, name))) == 0
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
    # declining to remove the only copy isn't a failure
    assert cli("archive", "prune", a.name, str(listing(tmp_path, name))) == 0
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
    assert cli("archive", "prune", archive.name, str(listing(tmp_path, missing))) == 1
    assert f"{missing}: not in registry" in caplog.text
    assert cli("archive", "prune", unique("arch"), str(listing(tmp_path, missing))) == 1
    assert "No such archive" in caplog.text


def test_register_tar(
    cli, registry, client, archive, dtype, deposit_file, tmp_path, unique, caplog
):
    name = deposit_file(archive, dtype)
    other = tmp_path / f"{unique('other')}.txt"
    other.write_text("not in the registry")
    tar = make_tar(tmp_path / "archive.tar", stored_path(archive, name), other)
    tape = unique("tape")
    assert cli("archive", "register-tar", "-n", tape, "tape01", "3", str(tar)) == 0
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

    assert cli("archive", "import-tar", str(tar), str(b.path)) == 0
    assert stored_path(b, name).read_text() == "the contents"
    assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}
    assert "not in the registry" in caplog.text

    caplog.clear()
    assert cli("archive", "import-tar", str(tar), str(b.path)) == 0
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
    assert cli("archive", "import-tar", str(tar), str(b.path)) == 1
    assert "the registry refused the location" in caplog.text
    assert "Authentication credentials were not provided." in caplog.text
    with pytest.raises(FileNotFoundError):
        stored_path(b, name)


def test_import_tar_file_already_there(
    cli, registry, two_archives, dtype, deposit_file, tmp_path, caplog
):
    # a file for the resource is in the destination, but not registered there
    a, b = two_archives
    name = deposit_file(a, dtype)
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name))
    copy = tmp_path / "copy.txt"
    copy.write_text(name)
    nbank_archive.store_resource(b.config, copy, id=name)
    assert cli("archive", "import-tar", str(tar), str(b.path)) == 1
    assert "is already in the archive" in caplog.text
    assert core.describe(registry.url, name)["locations"] == [a.name]


@pytest.fixture
def dir_archives(make_archive):
    return (
        make_archive(allow_directories=True, require_hash=True),
        make_archive(allow_directories=True, require_hash=False),
    )


def test_import_tar_directory_resource(
    cli, registry, dir_archives, dtype, unique, tmp_path, caplog
):
    a, b = dir_archives
    src = tmp_path / unique("res")
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("inside")
    (src / "top").write_text("top")
    [item] = core.deposit(a.path, [src], dtype=dtype, auth=registry.auth)
    name = item["id"]
    tar = tmp_path / "archive.tar"
    with tarfile.open(tar, "w") as t:
        t.add(stored_path(a, name), arcname=name)
    assert cli("archive", "import-tar", str(tar), str(b.path)) == 0
    assert (stored_path(b, name) / "sub" / "data").read_text() == "inside"
    assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}
    assert cli("check", "archive", str(b.path)) == 0


def test_export_and_import(
    cli, registry, dir_archives, dtype, deposit_file, unique, tmp_path, caplog
):
    a, b = dir_archives
    file_name = deposit_file(a, dtype, contents=unique("file contents"))
    src = tmp_path / unique("res")
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text(unique("inside"))
    [item] = core.deposit(a.path, [src], dtype=dtype, auth=registry.auth)
    dir_name = item["id"]
    missing = unique("missing")
    tar = tmp_path / "export.tar"
    assert cli("export", "--manifest", str(tar), file_name, dir_name, missing) == 1
    assert f"{missing} -> not in the registry" in caplog.text
    with tarfile.open(tar) as t:
        assert t.getnames()[-1] == "manifest.json"
        manifest = json.load(t.extractfile("manifest.json"))
    assert manifest["registry"] == registry.url
    entries = {e["name"]: e for e in manifest["resources"]}
    assert set(entries) == {file_name, dir_name}
    assert entries[file_name]["dtype"] == dtype
    assert entries[dir_name]["path"] == dir_name
    assert "locations" not in entries[file_name]
    assert cli("check", "tar", str(tar)) == 0

    assert cli("archive", "import-tar", str(tar), str(b.path)) == 0
    assert (
        stored_path(b, file_name).read_bytes() == stored_path(a, file_name).read_bytes()
    )
    assert (stored_path(b, dir_name) / "sub" / "data").read_text().startswith("inside")
    for name in (file_name, dir_name):
        assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}
    assert cli("check", "archive", str(b.path)) == 0


@pytest.mark.parametrize("out_name", ["export.zip", "export"])
def test_export_zip_and_directory(
    cli, registry, dir_archives, dtype, deposit_file, unique, tmp_path, out_name
):
    import zipfile

    a, _ = dir_archives
    file_name = deposit_file(a, dtype)
    src = tmp_path / unique("res")
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text(unique("inside"))
    [item] = core.deposit(a.path, [src], dtype=dtype, auth=registry.auth)
    dir_name = item["id"]
    out = tmp_path / out_name
    assert cli("export", str(out), file_name, dir_name) == 0
    file_stored = stored_path(a, file_name)
    if out.suffix == ".zip":
        with zipfile.ZipFile(out) as zf:
            assert zf.read(file_stored.name) == file_stored.read_bytes()
            assert f"{dir_name}/sub/data" in zf.namelist()
    else:
        assert (out / file_stored.name).read_bytes() == file_stored.read_bytes()
        assert util.hash_directory(out / dir_name) == util.hash_directory(
            stored_path(a, dir_name)
        )


def test_copy(
    cli, registry, dir_archives, dtype, deposit_file, unique, tmp_path, caplog
):
    a, b = dir_archives
    file_name = deposit_file(a, dtype)
    src = tmp_path / unique("res")
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text(unique("inside"))
    [item] = core.deposit(a.path, [src], dtype=dtype, auth=registry.auth)
    dir_name = item["id"]
    missing = unique("missing")

    assert cli("copy", "-y", str(b.path), file_name, dir_name) == 0
    assert core.describe(registry.url, file_name)["locations"] == [a.name]
    with pytest.raises(FileNotFoundError):
        stored_path(b, file_name)

    assert cli("copy", str(b.path), file_name, dir_name, missing) == 1
    assert f"{missing} -> not in the registry" in caplog.text
    assert "copied: 2" in caplog.text
    assert (
        stored_path(b, file_name).read_bytes() == stored_path(a, file_name).read_bytes()
    )
    assert util.hash_directory(stored_path(b, dir_name)) == util.hash_directory(
        stored_path(a, dir_name)
    )
    for name in (file_name, dir_name):
        assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}
    assert cli("check", "archive", str(b.path)) == 0

    caplog.clear()
    assert cli("copy", str(b.path), file_name, dir_name) == 0
    assert "already in destination: 2" in caplog.text


def test_copy_from_archive(
    cli, registry, make_archive, dtype, deposit_file, replicate, caplog
):
    a, b, empty, dest = (make_archive(require_hash=False) for _ in range(4))
    name = deposit_file(a, dtype)
    replicate(name, a, b)
    assert cli("copy", "-a", empty.name, str(dest.path), name) == 1
    assert f"not in archive '{empty.name}'" in caplog.text
    assert cli("copy", "-a", dest.name, str(dest.path), name) == 1
    assert "the source and destination archives are the same" in caplog.text
    assert cli("copy", "-a", b.name, str(dest.path), name) == 0
    assert set(core.describe(registry.url, name)["locations"]) == {
        a.name,
        b.name,
        dest.name,
    }


def test_copy_changed_source(cli, registry, two_archives, dtype, deposit_file, caplog):
    a, b = two_archives
    name = deposit_file(a, dtype, hash=True)
    stored = stored_path(a, name)
    stored.chmod(0o644)
    stored.write_text("changed")
    assert cli("copy", str(b.path), name) == 1
    assert "don't match" in caplog.text
    assert core.describe(registry.url, name)["locations"] == [a.name]
    with pytest.raises(FileNotFoundError):
        stored_path(b, name)


def test_copy_directory_to_archive_without_directories(
    cli, registry, dir_archives, make_archive, dtype, unique, tmp_path, caplog
):
    a, _ = dir_archives
    b = make_archive(require_hash=False)
    src = tmp_path / unique("res")
    src.mkdir()
    (src / "data").write_text(unique("inside"))
    [item] = core.deposit(a.path, [src], dtype=dtype, auth=registry.auth)
    assert cli("copy", str(b.path), item["id"]) == 1
    assert "doesn't allow directory resources" in caplog.text


def test_import_tar_read_error(
    cli, registry, two_archives, dtype, deposit_file, unique, failing_tar, caplog
):
    a, b = two_archives
    first = deposit_file(a, dtype)
    second = deposit_file(a, dtype, contents=unique("big") + "x" * 50_000)
    members = [
        (stored_path(a, name).name, stored_path(a, name).read_bytes())
        for name in (first, second)
    ]
    # fails partway through the second resource
    path = failing_tar(members, 3 * 10240)
    assert cli("archive", "import-tar", str(path), str(b.path)) == 1
    assert f"✗ {members[1][0]} -> unable to read; stopping" in caplog.text
    assert "Input/output error after reading 30720 bytes in 3 blocks" in caplog.text
    assert stored_path(b, first).read_bytes() == members[0][1]
    with pytest.raises(FileNotFoundError):
        stored_path(b, second)
    assert core.describe(registry.url, second)["locations"] == [a.name]
    assert cli("check", "archive", str(b.path)) == 0


def changed_tar(archive, name, tmp_path):
    """A tar file holding a changed copy of a resource under its own name."""
    stored = stored_path(archive, name)
    changed = tmp_path / stored.name
    changed.write_text("changed")
    return make_tar(tmp_path / "archive.tar", changed)


def test_import_tar_hash_mismatch(
    cli, registry, two_archives, dtype, deposit_file, tmp_path, caplog
):
    a, b = two_archives
    name = deposit_file(a, dtype, hash=True)
    tar = changed_tar(a, name, tmp_path)
    assert cli("archive", "import-tar", str(tar), str(b.path)) == 1
    assert "don't match the registered hash" in caplog.text
    with pytest.raises(FileNotFoundError):
        stored_path(b, name)
    assert core.describe(registry.url, name)["locations"] == [a.name]


def test_import_tar_dry_run_checks_hashes(
    cli, two_archives, dtype, deposit_file, tmp_path, caplog
):
    a, b = two_archives
    name = deposit_file(a, dtype, hash=True)
    tar = make_tar(tmp_path / "good.tar", stored_path(a, name))
    assert cli("archive", "import-tar", "-y", str(tar), str(b.path)) == 0
    assert f"{stored_path(a, name).name} -> OK" in caplog.text
    (tmp_path / "bad").mkdir()
    bad = changed_tar(a, name, tmp_path / "bad")
    assert cli("archive", "import-tar", "-y", str(bad), str(b.path)) == 1
    assert "don't match the registered hash" in caplog.text


def test_import_tar_from_stdin(
    cli, registry, two_archives, dtype, deposit_file, tmp_path, monkeypatch
):
    a, b = two_archives
    name = deposit_file(a, dtype, hash=True)
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name))
    with open(tar, "rb") as fp:
        monkeypatch.setattr("sys.stdin", type("Stdin", (), {"buffer": fp}))
        assert cli("archive", "import-tar", "-", str(b.path)) == 0
    assert set(core.describe(registry.url, name)["locations"]) == {a.name, b.name}


def test_import_tar_full_paths(
    cli, registry, two_archives, dtype, deposit_file, tmp_path
):
    # as written by `nbank locate -0 | xargs -0 tar -cf`
    a, b = two_archives
    name = deposit_file(a, dtype, hash=True)
    stored = stored_path(a, name)
    tar = tmp_path / "archive.tar"
    with tarfile.open(tar, "w") as t:
        t.add(stored, arcname=str(stored).lstrip("/"))
    assert cli("archive", "import-tar", str(tar), str(b.path)) == 0
    assert stored_path(b, name).read_text() == stored.read_text()


def test_import_tar_shows_progress(
    cli, two_archives, dtype, deposit_file, tmp_path, monkeypatch
):
    import io

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    a, b = two_archives
    name = deposit_file(a, dtype, hash=True)
    stored = stored_path(a, name)
    tar = make_tar(tmp_path / "archive.tar", stored)
    terminal = Terminal()
    monkeypatch.setattr("sys.stderr", terminal)
    assert cli("archive", "import-tar", "-y", str(tar), str(b.path)) == 0
    size = stored.stat().st_size
    assert f"  {stored.name}  {size} B / {size} B (100%)" in terminal.getvalue()


def test_check_tar(cli, two_archives, dtype, deposit_file, unique, tmp_path, caplog):
    a, b = two_archives
    hashed = deposit_file(a, dtype, hash=True)
    unhashed = deposit_file(b, dtype)
    other = tmp_path / f"{unique('other')}.txt"
    other.write_text("not in the registry")
    tar = make_tar(
        tmp_path / "archive.tar",
        stored_path(a, hashed),
        stored_path(b, unhashed),
        other,
    )
    assert cli("check", "tar", str(tar)) == 0
    assert f"{stored_path(a, hashed).name} -> OK" in caplog.text
    size = stored_path(a, hashed).stat().st_size
    assert re.search(
        rf"{re.escape(stored_path(a, hashed).name)} -> OK  \({size} B, [\d.]+ .?B/s\)",
        caplog.text,
    )
    assert (
        f"{stored_path(b, unhashed).name} -> OK (no registered hash to check)"
        in caplog.text
    )
    assert f"{other.name} -> '{other.stem}' not in the registry" in caplog.text
    assert (
        "Resources checked: 2; failed: 0; no registered hash: 1; "
        "files not in the registry: 1"
    ) in caplog.text


def test_check_tar_hash_mismatch(cli, archive, dtype, deposit_file, tmp_path, caplog):
    name = deposit_file(archive, dtype, hash=True)
    tar = changed_tar(archive, name, tmp_path)
    assert cli("check", "tar", str(tar)) == 1
    assert "don't match the registered hash" in caplog.text
    assert "failed: 1" in caplog.text


def test_check_tar_directory_resource(
    cli, registry, dir_archives, dtype, unique, tmp_path, caplog
):
    a, _ = dir_archives
    src = tmp_path / unique("res")
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text(unique("inside"))
    [item] = core.deposit(a.path, [src], dtype=dtype, auth=registry.auth)
    name = item["id"]
    tar = tmp_path / "archive.tar"
    with tarfile.open(tar, "w") as t:
        t.add(stored_path(a, name), arcname=name)
    assert cli("check", "tar", str(tar)) == 0
    assert f"{name} -> OK" in caplog.text


def test_check_tar_from_stdin(
    cli, archive, dtype, deposit_file, tmp_path, monkeypatch, caplog
):
    name = deposit_file(archive, dtype, hash=True)
    tar = make_tar(tmp_path / "archive.tar", stored_path(archive, name))
    with open(tar, "rb") as fp:
        monkeypatch.setattr("sys.stdin", type("Stdin", (), {"buffer": fp}))
        assert cli("check", "tar", "-") == 0
    assert f"{stored_path(archive, name).name} -> OK" in caplog.text


def test_check_tar_truncated(
    cli, archive, dtype, deposit_file, unique, tmp_path, caplog
):
    name = deposit_file(archive, dtype, hash=True, contents=unique("x") * 10_000)
    tar = make_tar(tmp_path / "archive.tar", stored_path(archive, name))
    truncated = tmp_path / "truncated.tar"
    truncated.write_bytes(tar.read_bytes()[:50_000])
    assert cli("check", "tar", str(truncated)) == 1
    assert "unable to read" in caplog.text


def test_import_tar_truncated(
    cli, two_archives, dtype, deposit_file, unique, tmp_path, caplog
):
    a, b = two_archives
    name = deposit_file(a, dtype, hash=True, contents=unique("x") * 10_000)
    tar = make_tar(tmp_path / "archive.tar", stored_path(a, name))
    truncated = tmp_path / "truncated.tar"
    truncated.write_bytes(tar.read_bytes()[:50_000])
    assert cli("archive", "import-tar", str(truncated), str(b.path)) == 1
    assert "unable to read" in caplog.text


@pytest.fixture
def tape(cli, archive, dtype, deposit_file, unique, tmp_path):
    """A tar file of two resources, registered as a tape archive."""
    names = [deposit_file(archive, dtype, hash=True) for _ in range(2)]
    tar = make_tar(tmp_path / "tape.tar", *(stored_path(archive, n) for n in names))
    tape_name = unique("tape")
    # archive roots (tape label and file number) must be unique too
    label = unique("label")
    assert cli("archive", "register-tar", "-n", tape_name, label, "1", str(tar)) == 0
    return SimpleNamespace(name=tape_name, tar=tar, resources=names)


def test_check_tar_archive_complete(cli, tape, caplog):
    caplog.clear()
    assert cli("check", "tar", "--archive", tape.name, str(tape.tar)) == 0
    assert f"Missing from the tar file: 0; not registered to {tape.name}: 0" in (
        caplog.text
    )


def test_check_tar_archive_missing(cli, register, tape, caplog):
    # the registry says this resource is on the tape, but it isn't
    lost = register(archive=tape.name)["name"]
    caplog.clear()
    assert cli("check", "tar", "--archive", tape.name, str(tape.tar)) == 1
    assert f"  ✗ {lost}: registered to {tape.name} but MISSING" in caplog.text
    assert "Missing from the tar file: 1" in caplog.text


def test_check_tar_archive_not_registered(
    cli, archive, dtype, deposit_file, tape, tmp_path, caplog
):
    extra = deposit_file(archive, dtype, hash=True)
    tar = make_tar(
        tmp_path / "more.tar",
        *(stored_path(archive, n) for n in [*tape.resources, extra]),
    )
    caplog.clear()
    assert cli("check", "tar", "--archive", tape.name, str(tar)) == 0
    assert (
        f"{stored_path(archive, extra).name} -> not registered to {tape.name}"
    ) in caplog.text
    assert f"not registered to {tape.name}: 1" in caplog.text


def test_check_tar_unknown_archive(cli, tape, unique, caplog):
    missing = unique("tape")
    assert cli("check", "tar", "--archive", missing, str(tape.tar)) == 1
    assert f"no archive '{missing}' in the registry" in caplog.text
