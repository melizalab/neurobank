# -*- mode: python -*-
"""Tests of the commands that keep an archive and the registry consistent."""

import json
import shutil
import tarfile
from types import SimpleNamespace

import pytest

from nbank import archive as nbank_archive
from nbank import check, core, script, transfer
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
    assert f" - {orphan}: has NO locations" in caplog.text
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
    path = stub / transfer.partial_name("res_1.wav")
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
