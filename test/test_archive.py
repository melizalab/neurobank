# -*- mode: python -*-
import logging
import os
import stat

import pytest

from nbank import archive, check, util
from nbank.types import LocationContext
from test import place_resource, running_as_root, skip_unless_posix

dummy_registry = "https://localhost:8000/neurobank"


@pytest.fixture()
def tmp_archive(tmp_path):
    skip_unless_posix()
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry)


def test_invalid_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        _ = archive.get_config(tmp_path)


def test_can_read_config(tmp_archive):
    cfg = archive.get_config(tmp_archive["path"])
    assert tmp_archive == cfg


def test_archive_umask(tmp_archive):
    root = tmp_archive["path"]
    mode = (root / archive._resource_subdir).stat().st_mode
    assert tmp_archive["policy"]["access"]["umask"] == archive._default_umask
    # setgid only on Linux; BSD and macOS always inherit the directory's group
    assert mode & 0o7000 == archive._setgid
    assert mode & archive._default_umask == 0


def test_store_and_find_resource(tmp_archive, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, id=name)
    # assertions
    assert not src.exists()
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    mode = path.stat().st_mode
    assert mode & tmp_archive["policy"]["access"]["umask"] == 0
    assert path.read_text() == contents


def test_parse_neurobank_location(archive_layout):
    from nbank.util import parse_location

    name = "dummy_1"
    path = place_resource(archive_layout, name, name)
    location = {
        "scheme": "neurobank",
        "root": archive_layout["path"],
        "resource_name": name,
        "key": name,
    }
    res = parse_location(location)
    assert isinstance(res, archive.Resource)
    assert res.path == path


@pytest.fixture
def stored_wav(archive_layout):
    """res_1, stored as res_1.wav, and a location for it without a key."""
    path = place_resource(archive_layout, "res_1", "res_1.wav")
    location = {
        "scheme": "neurobank",
        "root": str(archive_layout["path"]),
        "resource_name": "res_1",
    }
    return path, location


def test_location_key_names_the_file(stored_wav):
    path, location = stored_wav
    resource = archive.Resource.from_location(
        {**location, "key": "res_1.wav"}, LocationContext()
    )
    assert resource.path == path


@pytest.mark.parametrize(
    "key",
    [
        None,
        # no file has that name, and other names aren't tried
        "res_1",
        "res_1.json",
        # a key is a name, not a path
        "../re/res_1.wav",
        "re/res_1.wav",
        "",
    ],
)
def test_location_key_not_found(stored_wav, key):
    _, location = stored_wav
    assert (
        archive.Resource.from_location({**location, "key": key}, LocationContext())
        is None
    )


def test_location_key_alt_base(stored_wav, archive_layout, tmp_path):
    path, location = stored_wav
    # the archive is mounted under another directory on this host
    alt_base = tmp_path / "mnt"
    alt_base.mkdir()
    archive_layout["path"].rename(alt_base / "archive")
    resource = util.parse_location({**location, "key": "res_1.wav"}, alt_base=alt_base)
    assert resource.path == alt_base / path.relative_to(tmp_path)


def test_store_and_fetch_resource(tmp_archive, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, id=name)
    resource = archive.Resource(tmp_archive["path"], name, name)
    fetched = resource.fetch(tmp_path)
    assert fetched.is_file()
    assert fetched.read_text() == contents


def test_store_and_find_named_resource(tmp_archive, tmp_path):
    name = "dummy_2"
    src = tmp_path / "tempfile"
    contents = '{"foo": 20}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, id=name)
    # assertions
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    assert path.name == name
    assert path.read_text() == contents


def test_store_and_find_resource_with_extension(tmp_archive, tmp_path):
    name = "dummy_3"
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, id=name)
    # assertions
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    assert path.stem == name
    assert path.suffix == src.suffix
    assert path.read_text() == contents


def test_cannot_store_duplicate_resource(tmp_archive, tmp_path):
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src)
    src.write_text(contents)
    with pytest.raises(KeyError):
        archive.store_resource(tmp_archive, src)


def test_cannot_store_duplicate_basenames(tmp_archive, tmp_path):
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src)
    path = archive.resource_path(tmp_archive, "temp", resolve_ext=True)
    assert path.name == src.name
    src.write_text(contents)
    with pytest.raises(KeyError):
        archive.store_resource(tmp_archive, src, id="temp.txt")


def test_cannot_store_resource_twice_with_different_extensions(tmp_archive, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.wav"
    first.write_text("json")
    second.write_text("wav")
    stored = archive.store_resource(tmp_archive, first, id="res_1")
    with pytest.raises(KeyError, match="already stored"):
        archive.store_resource(tmp_archive, second, id="res_1")
    assert list(stored.parent.iterdir()) == [stored]
    assert second.exists()


def test_cannot_violate_directory_policy(tmp_archive, tmp_path):
    dir = tmp_path / "tempdir"
    dir.mkdir()
    with pytest.raises(TypeError):
        archive.store_resource(tmp_archive, dir)


@pytest.mark.posix
def test_keep_extensions_false_is_ignored(tmp_path, caplog):
    # set by archives made before the policy was retired
    root = tmp_path / "archive"
    archive.create(root, dummy_registry, keep_extensions=False)
    cfg = archive.get_config(root)
    assert "keep_extensions policy is no longer supported" in caplog.text
    src = tmp_path / "temp.wav"
    src.write_text("not a wave file")
    stored = archive.store_resource(cfg, src, id="dummy_3")
    assert stored.name == "dummy_3.wav"


def test_new_archives_have_no_keep_extensions_policy(tmp_archive, caplog):
    assert "keep_extensions" not in tmp_archive["policy"]
    archive.get_config(tmp_archive["path"])
    assert "keep_extensions" not in caplog.text


def test_verify_permissions_source_unreadable(tmp_path, tmp_archive):
    unreadable = tmp_path / "unreadable"
    unreadable.write_text("blah")
    unreadable.chmod(0o000)
    with pytest.raises(PermissionError, match=str(unreadable)):
        archive.verify_permissions(tmp_archive, unreadable)


def test_verify_permissions_resource_dir_missing(tmp_archive, tmp_path):
    dummy = tmp_path / "dummy"
    dummy.write_text("blah")
    tgt_base = tmp_archive["path"] / archive._resource_subdir
    tgt_base.rmdir()
    with pytest.raises(PermissionError, match=str(tgt_base)):
        archive.verify_permissions(tmp_archive, dummy)


def test_verify_permissions_resource_dir_denied(tmp_archive, tmp_path):
    dummy = tmp_path / "dummy"
    dummy.write_text("blah")
    tgt_base = tmp_archive["path"] / archive._resource_subdir
    mode = tgt_base.stat().st_mode
    tgt_base.chmod(0o500)  # readable and searchable, but not writable
    try:
        with pytest.raises(PermissionError, match=str(tgt_base)):
            archive.verify_permissions(tmp_archive, dummy)
    finally:
        tgt_base.chmod(mode)


def test_verify_permissions_file_exists(tmp_archive, tmp_path):
    name = "dummy"
    dummy_1 = tmp_path / name
    dummy_1.write_text("blah")
    stored_path = archive.store_resource(tmp_archive, dummy_1, id=name)
    dummy_2 = tmp_path / name
    dummy_2.write_text("blah")
    with pytest.raises(FileExistsError, match=str(stored_path)):
        archive.verify_permissions(tmp_archive, dummy_2)
    # should also raise if the id matches but not the filename
    dummy_3 = tmp_path / "dummy_3"
    dummy_3.write_text("blah")
    with pytest.raises(FileExistsError, match=str(stored_path)):
        archive.verify_permissions(tmp_archive, dummy_3, id=name)


def test_verify_permissions_subdirectory_denied(tmp_archive, tmp_path):
    name = "dummy_100"
    dummy = tmp_path / "dummy"
    dummy.write_text("blah")
    tgt_sub = tmp_archive["path"] / archive._resource_subdir / archive.id_stub(name)
    tgt_sub.mkdir()
    mode = tgt_sub.stat().st_mode
    tgt_sub.chmod(0o500)  # readable and searchable, but not writable
    try:
        with pytest.raises(PermissionError, match=str(tgt_sub)):
            archive.verify_permissions(tmp_archive, dummy, name)
    finally:
        tgt_sub.chmod(mode)


def test_verify_permissions_ok(tmp_archive, tmp_path):
    name = "dummy_100"
    dummy = tmp_path / "dummy"
    dummy.write_text("blah")
    # id has no subdirectory yet: only the resources dir itself is checked
    assert archive.verify_permissions(tmp_archive, dummy, name) is None
    # id's subdirectory exists and has correct permissions
    tgt_sub = tmp_archive["path"] / archive._resource_subdir / archive.id_stub(name)
    tgt_sub.mkdir()
    assert archive.verify_permissions(tmp_archive, dummy, name) is None


@pytest.mark.skipif(running_as_root, reason="root can read anything")
def test_verify_permissions_checks_directory_contents(tmp_archive, tmp_path):
    # a directory is deposited as a zip of its contents, so they must be readable
    dname = tmp_path / "tempdir"
    dname.mkdir()
    (dname / "readable").write_text("blah")
    assert archive.verify_permissions(tmp_archive, dname, "dummy_1") is None
    unreadable = dname / "unreadable"
    unreadable.write_text("blah")
    unreadable.chmod(0o000)
    with pytest.raises(PermissionError, match=str(unreadable)):
        archive.verify_permissions(tmp_archive, dname, "dummy_1")


def test_store_resource_copies_when_source_dir_not_writable(
    tmp_archive, tmp_path, caplog
):
    src_dir = tmp_path / "readonly_dir"
    src_dir.mkdir()
    src = src_dir / "dummy"
    src.write_text("blah")
    src_dir.chmod(0o500)  # readable and searchable, but not writable
    try:
        with caplog.at_level(logging.WARNING, logger="nbank"):
            tgt = archive.store_resource(tmp_archive, src, id="dummy_1")
    finally:
        src_dir.chmod(0o700)
    assert tgt.read_text() == "blah"
    assert src.exists()  # left in place, not removed
    assert "not writable" in caplog.text


@pytest.fixture
def restrictive_umask():
    old = os.umask(0o077)
    yield
    os.umask(old)


@pytest.mark.posix
def test_store_resource_applies_mode_policy(tmp_path, restrictive_umask):
    # the depositor's umask shouldn't leave files or directories less
    # accessible than the archive's policy requires
    cfg = archive.create(tmp_path / "archive", dummy_registry, umask=0o002)
    src = tmp_path / "res_1"
    src.write_text("contents")
    path = archive.store_resource(cfg, src)
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o777 == 0o775
    assert list(check.check_archive_permissions(cfg)) == []


@pytest.mark.posix
def test_verify_no_symlinks(tmp_path):
    real = tmp_path / "real"
    real.write_text("contents")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("contents")
    assert archive.verify_no_symlinks(real) is None
    assert archive.verify_no_symlinks(src) is None

    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match=str(link)):
        archive.verify_no_symlinks(link)

    nested = src / "sub" / "link"
    nested.symlink_to(real)
    with pytest.raises(ValueError, match=str(nested)):
        archive.verify_no_symlinks(src)
    nested.unlink()

    dir_link = src / "dirlink"
    dir_link.symlink_to(src / "sub")
    with pytest.raises(ValueError, match=str(dir_link)):
        archive.verify_no_symlinks(src)


@pytest.mark.skipif(running_as_root, reason="root can change ownership")
def test_permission_fixer_changes_mode_when_chown_fails(tmp_archive, tmp_path, caplog):
    import grp

    if os.getgid() == 0 or 0 in os.getgroups():
        pytest.skip("need a group this user doesn't belong to")
    src = tmp_path / "res_1"
    src.write_text("contents")
    path = archive.store_resource(tmp_archive, src)
    path.chmod(0o600)
    tmp_archive["policy"]["access"]["group"] = grp.getgrgid(0).gr_name
    with caplog.at_level(logging.WARNING, logger="nbank"):
        archive.permission_fixer(tmp_archive)(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert "unable to change uid/gid" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nbank"):
        archive.permission_fixer(tmp_archive, quiet=True)(path)
    assert caplog.text == ""


@pytest.mark.posix
def test_create_resources_directory_mode(tmp_path, restrictive_umask):
    cfg = archive.create(tmp_path / "archive", dummy_registry, umask=0o002)
    resdir = cfg["path"] / "resources"
    required, _ = archive.mode_policy(cfg, resdir)
    assert stat.S_IMODE(resdir.stat().st_mode) == required
    assert list(check.check_archive_permissions(cfg)) == []


@pytest.mark.posix
def test_create_staging_directory(tmp_path, restrictive_umask):
    cfg = archive.create(tmp_path / "archive", dummy_registry, umask=0o002)
    staging = archive.staging_dir(cfg)
    resdir = cfg["path"] / "resources"
    assert staging.is_dir()
    # the same group and mode as the resources directory
    assert stat.S_IMODE(staging.stat().st_mode) == stat.S_IMODE(resdir.stat().st_mode)
    assert staging.stat().st_gid == resdir.stat().st_gid
    assert "staging/" in (cfg["path"] / ".gitignore").read_text().split()


@pytest.mark.posix
def test_create_shared_archive(tmp_path):
    cfg = archive.create(tmp_path / "archive", dummy_registry, shared=True)
    assert cfg["policy"]["access"]["user"] is None
    src = tmp_path / "res_1"
    src.write_text("contents")
    archive.store_resource(cfg, src)
    assert list(check.check_archive_permissions(cfg)) == []


@pytest.mark.posix
@pytest.mark.skipif(running_as_root, reason="root can own anything")
def test_owner_is_checked_unless_shared(tmp_path):
    import pwd

    cfg = archive.create(tmp_path / "archive", dummy_registry)
    cfg["policy"]["access"]["user"] = pwd.getpwuid(0).pw_name
    statuses = {f.status for f in check.check_archive_permissions(cfg)}
    assert check.Status.WRONG_OWNER in statuses
    cfg["policy"]["access"]["user"] = None
    assert list(check.check_archive_permissions(cfg)) == []


@pytest.mark.posix
def test_create_with_group(tmp_path):
    import grp

    group = grp.getgrgid(os.getgid()).gr_name
    cfg = archive.create(tmp_path / "archive", dummy_registry, group=group)
    assert cfg["policy"]["access"]["group"] == group
    for path in (
        cfg["path"] / "resources",
        cfg["path"] / "nbank.json",
        cfg["path"] / "README.md",
    ):
        assert path.stat().st_gid == os.getgid()


@pytest.mark.posix
def test_create_with_unknown_group(tmp_path):
    root = tmp_path / "archive"
    with pytest.raises(ValueError, match="no-such-group-xyzzy"):
        archive.create(root, dummy_registry, group="no-such-group-xyzzy")
    assert not root.exists()


@pytest.mark.posix
@pytest.mark.skipif(running_as_root, reason="root can change any group")
def test_create_with_group_not_a_member(tmp_path, caplog):
    import grp

    if os.getgid() == 0 or 0 in os.getgroups():
        pytest.skip("need a group this user doesn't belong to")
    group = grp.getgrgid(0).gr_name
    with caplog.at_level(logging.WARNING, logger="nbank"):
        cfg = archive.create(tmp_path / "archive", dummy_registry, group=group)
    assert cfg["policy"]["access"]["group"] == group
    assert "unable to change the group of" in caplog.text
    assert "unable to change uid/gid of" in caplog.text


@pytest.mark.parametrize("user", [None, "root"])
def test_permission_fixer_as_root(tmp_archive, monkeypatch, user):
    import grp
    import pwd

    tmp_archive["policy"]["access"]["user"] = user
    calls = []
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: calls.append((uid, gid)))
    path = tmp_archive["path"] / "resources"
    archive.permission_fixer(tmp_archive)(path)
    gid = grp.getgrnam(tmp_archive["policy"]["access"]["group"]).gr_gid
    expected_uid = -1 if user is None else pwd.getpwnam(user).pw_uid
    assert calls == [(expected_uid, gid)]


@pytest.mark.posix
@pytest.mark.parametrize("existing", ["nbank.json", "README.md", ".gitignore"])
def test_create_does_not_overwrite(tmp_path, existing):
    root = tmp_path / "archive"
    root.mkdir()
    (root / existing).write_text("keep me")
    with pytest.raises(FileExistsError, match=existing):
        archive.create(root, dummy_registry)
    assert [p.name for p in root.iterdir()] == [existing]
    assert (root / existing).read_text() == "keep me"


@pytest.mark.posix
def test_create_in_empty_directory(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    cfg = archive.create(root, dummy_registry)
    assert cfg["path"] == root


def test_create_defaults_to_read_only_resources(tmp_archive):
    assert tmp_archive["policy"]["access"]["read_only_resources"] is True


def test_mode_policy_read_only_resources(tmp_archive):
    umask = tmp_archive["policy"]["access"]["umask"]
    stub = tmp_archive["path"] / "resources" / "re"
    stub.mkdir()
    resource = stub / "res_1"
    resource.write_text("contents")
    # the archive layout stays writable so resources can be added
    assert archive.mode_policy(tmp_archive, stub)[1] == umask
    assert archive.mode_policy(tmp_archive, resource) == (
        0o444 & ~umask,
        umask | 0o222,
    )
    # archives without the setting keep writable resources
    del tmp_archive["policy"]["access"]["read_only_resources"]
    assert archive.mode_policy(tmp_archive, resource) == (0o444 & ~umask, umask)


def plant_directory(cfg, name="res_1"):
    """Puts a read-only directory where a resource would be stored, as a directory
    resource was."""
    path = archive.resource_path(cfg, name)
    (path / "sub").mkdir(parents=True)
    (path / "sub" / "data").write_text("contents")
    for p in (path / "sub" / "data", path / "sub", path):
        p.chmod(0o444 if p.is_file() else 0o555)
    return path


def test_remove_read_only_directory(tmp_archive):
    path = plant_directory(tmp_archive)
    resource = archive.Resource(tmp_archive["path"], "res_1", "res_1")
    assert resource.deletable
    resource.unlink()
    assert not path.exists()


def test_cannot_remove_others_read_only_directory(tmp_archive, tmp_path, monkeypatch):
    path = plant_directory(tmp_archive)
    src = tmp_path / "res_2"
    src.write_text("contents")
    file_path = archive.store_resource(tmp_archive, src)
    # pretend to be some other (non-root) user in the archive's group
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    assert not archive._can_remove(path)
    # a file only needs its directory to be writable
    assert archive._can_remove(file_path)


@pytest.mark.posix
def test_fix_makes_existing_resources_read_only(tmp_path):
    cfg = archive.create(
        tmp_path / "archive", dummy_registry, read_only_resources=False
    )
    src = tmp_path / "res_1"
    src.write_text("contents")
    src.chmod(0o644)
    path = archive.store_resource(cfg, src)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    cfg["policy"]["access"]["read_only_resources"] = True
    findings = list(check.check_archive_permissions(cfg, fix=True))
    assert [(f.status, f.path, f.fixed) for f in findings] == [
        (check.Status.WRONG_MODE, path, True)
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
