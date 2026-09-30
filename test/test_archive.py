# -*- mode: python -*-
import logging
import os
import stat

import pytest

from nbank import archive, check

dummy_registry = "https://localhost:8000/neurobank"


@pytest.fixture()
def tmp_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry)


@pytest.fixture()
def tmp_dir_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry, allow_directories=True)


@pytest.fixture()
def tmp_noext_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry, keep_extensions=False)


def test_invalid_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        _ = archive.get_config(tmp_path)


def test_can_read_config(tmp_archive):
    cfg = archive.get_config(tmp_archive["path"])
    assert tmp_archive == cfg


def test_archive_umask(tmp_archive):
    # cfgtmpl = json.loads(archive._nbank_json)
    root = tmp_archive["path"]
    mode = (root / archive._resource_subdir).stat().st_mode
    assert tmp_archive["policy"]["access"]["umask"] == archive._default_umask
    assert mode & 0o7000 == 0o2000
    assert mode & archive._default_umask == 0


def test_store_and_find_resource(tmp_archive, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
    # assertions
    assert not src.exists()
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    mode = path.stat().st_mode
    assert mode & tmp_archive["policy"]["access"]["umask"] == 0
    assert path.read_text() == contents
    with pytest.deprecated_call():
        resources = list(archive.iter_resources(tmp_archive["path"]))
    assert resources == [path]


def test_parse_neurobank_location(tmp_archive, tmp_path):
    from nbank.util import parse_location

    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)

    location = {
        "scheme": "neurobank",
        "root": tmp_archive["path"],
        "resource_name": name,
    }
    res = parse_location(location)
    assert isinstance(res, archive.Resource)
    assert res.path == archive.resource_path(
        location["root"], location["resource_name"]
    )


def test_store_and_fetch_resource(tmp_archive, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
    resource = archive.Resource(tmp_archive["path"], name)
    fetched = resource.fetch(tmp_path)
    assert fetched.is_file()
    assert fetched.read_text() == contents


def test_store_and_find_named_resource(tmp_archive, tmp_path):
    name = "dummy_2"
    src = tmp_path / "tempfile"
    contents = '{"foo": 20}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
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
    archive.store_resource(tmp_archive, src, name)
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
        archive.store_resource(tmp_archive, src, "temp.txt")


def test_cannot_store_resource_twice_with_different_extensions(tmp_archive, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.wav"
    first.write_text("json")
    second.write_text("wav")
    stored = archive.store_resource(tmp_archive, first, "res_1")
    with pytest.raises(KeyError, match="already stored"):
        archive.store_resource(tmp_archive, second, "res_1")
    assert list(stored.parent.iterdir()) == [stored]
    assert second.exists()


def test_cannot_store_resource_twice_without_extensions(tmp_noext_archive, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.wav"
    first.write_text("json")
    second.write_text("wav")
    stored = archive.store_resource(tmp_noext_archive, first, "res_1")
    with pytest.raises(KeyError, match="already stored"):
        archive.store_resource(tmp_noext_archive, second, "res_1")
    assert list(stored.parent.iterdir()) == [stored]


def test_cannot_violate_directory_policy(tmp_archive, tmp_path):
    dir = tmp_path / "tempdir"
    dir.mkdir()
    with pytest.raises(TypeError):
        archive.store_resource(tmp_archive, dir)


def test_can_store_directories(tmp_dir_archive, tmp_path):
    id = "dummy_1"
    umask = tmp_dir_archive["policy"]["access"]["umask"]
    dname = tmp_path / "tempdir"
    fname = dname / "tempfile"
    dname.mkdir()
    fname.write_text("this is dumb")
    fname.chmod(0o777)
    assert (fname.stat().st_mode & umask) != 0

    archive.store_resource(tmp_dir_archive, dname, id)
    path = archive.resource_path(tmp_dir_archive, id, resolve_ext=True)
    assert path.is_dir()
    assert (path.stat().st_mode & umask) == 0

    fpath = path / "tempfile"
    assert fpath.is_file()
    assert (fpath.stat().st_mode & umask) == 0

    with pytest.deprecated_call():
        resources = list(archive.iter_resources(tmp_dir_archive["path"]))
    assert resources == [path]


def test_can_strip_extensions(tmp_noext_archive, tmp_path):
    name = "dummy_3"
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_noext_archive, src, name)
    path = archive.resource_path(tmp_noext_archive, name, resolve_ext=True)
    assert path.exists()
    assert path.suffix == ""


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


def test_verify_permissions_same_filesystem_skips_content_check(
    tmp_dir_archive, tmp_path
):
    # same-filesystem deposit is a plain rename, so unreadable contents don't
    # matter (archive.tmp_path and tmp_dir_archive's path share a filesystem)
    dname = tmp_path / "tempdir"
    dname.mkdir()
    (dname / "unreadable").write_text("blah")
    (dname / "unreadable").chmod(0o000)
    assert archive.verify_permissions(tmp_dir_archive, dname, "dummy_1") is None


def test_verify_permissions_cross_filesystem_checks_contents(
    monkeypatch, tmp_dir_archive, tmp_path
):
    monkeypatch.setattr(archive, "_same_filesystem", lambda a, b: False)
    dname = tmp_path / "tempdir"
    dname.mkdir()
    unreadable = dname / "unreadable"
    unreadable.write_text("blah")
    unreadable.chmod(0o000)
    with pytest.raises(PermissionError, match=str(unreadable)):
        archive.verify_permissions(tmp_dir_archive, dname, "dummy_1")


def test_verify_permissions_cross_filesystem_ok(monkeypatch, tmp_dir_archive, tmp_path):
    monkeypatch.setattr(archive, "_same_filesystem", lambda a, b: False)
    dname = tmp_path / "tempdir"
    dname.mkdir()
    (dname / "readable").write_text("blah")
    assert archive.verify_permissions(tmp_dir_archive, dname, "dummy_1") is None


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
            tgt = archive.store_resource(tmp_archive, src, "dummy_1")
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


def test_store_resource_leaves_symlink_targets_alone(tmp_dir_archive, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("not in the archive")
    outside.chmod(0o600)
    src = tmp_path / "res_1"
    src.mkdir()
    (src / "link").symlink_to(outside)
    archive.store_resource(tmp_dir_archive, src)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o600


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


@pytest.mark.skipif(os.getuid() == 0, reason="root can change ownership")
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


def test_create_resources_directory_mode(tmp_path, restrictive_umask):
    cfg = archive.create(tmp_path / "archive", dummy_registry, umask=0o002)
    resdir = cfg["path"] / "resources"
    required, _ = archive.mode_policy(cfg, resdir)
    assert stat.S_IMODE(resdir.stat().st_mode) == required
    assert list(check.check_archive_permissions(cfg)) == []


def test_create_shared_archive(tmp_path):
    cfg = archive.create(tmp_path / "archive", dummy_registry, shared=True)
    assert cfg["policy"]["access"]["user"] is None
    src = tmp_path / "res_1"
    src.write_text("contents")
    archive.store_resource(cfg, src)
    assert list(check.check_archive_permissions(cfg)) == []


@pytest.mark.skipif(os.getuid() == 0, reason="root can own anything")
def test_owner_is_checked_unless_shared(tmp_path):
    import pwd

    cfg = archive.create(tmp_path / "archive", dummy_registry)
    cfg["policy"]["access"]["user"] = pwd.getpwuid(0).pw_name
    statuses = {f.status for f in check.check_archive_permissions(cfg)}
    assert check.Status.WRONG_OWNER in statuses
    cfg["policy"]["access"]["user"] = None
    assert list(check.check_archive_permissions(cfg)) == []


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


def test_create_with_unknown_group(tmp_path):
    root = tmp_path / "archive"
    with pytest.raises(ValueError, match="no-such-group-xyzzy"):
        archive.create(root, dummy_registry, group="no-such-group-xyzzy")
    assert not root.exists()


@pytest.mark.skipif(os.getuid() == 0, reason="root can change any group")
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


@pytest.mark.parametrize("existing", ["nbank.json", "README.md", ".gitignore"])
def test_create_does_not_overwrite(tmp_path, existing):
    root = tmp_path / "archive"
    root.mkdir()
    (root / existing).write_text("keep me")
    with pytest.raises(FileExistsError, match=existing):
        archive.create(root, dummy_registry)
    assert [p.name for p in root.iterdir()] == [existing]
    assert (root / existing).read_text() == "keep me"


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


def make_directory_resource(cfg, tmp_path, name="res_1"):
    src = tmp_path / name
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("contents")
    return archive.store_resource(cfg, src)


def test_store_read_only_directory_resource(tmp_dir_archive, tmp_path):
    path = make_directory_resource(tmp_dir_archive, tmp_path)
    for p in (path, path / "sub"):
        assert stat.S_IMODE(p.stat().st_mode) & 0o222 == 0
    assert stat.S_IMODE((path / "sub" / "data").stat().st_mode) == 0o444
    # the subdirectory still accepts new resources
    other = tmp_path / "res_2"
    other.write_text("contents")
    archive.store_resource(tmp_dir_archive, other)
    assert list(check.check_archive_permissions(tmp_dir_archive)) == []


def test_remove_read_only_directory_resource(tmp_dir_archive, tmp_path):
    path = make_directory_resource(tmp_dir_archive, tmp_path)
    resource = archive.Resource(tmp_dir_archive["path"], "res_1")
    assert resource.deletable
    resource.unlink()
    assert not path.exists()


def test_cannot_remove_others_read_only_directory_resource(
    tmp_dir_archive, tmp_path, monkeypatch
):
    path = make_directory_resource(tmp_dir_archive, tmp_path)
    src = tmp_path / "res_2"
    src.write_text("contents")
    file_path = archive.store_resource(tmp_dir_archive, src)
    # pretend to be some other (non-root) user in the archive's group
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    assert not archive.can_remove(path)
    # a file only needs its directory to be writable
    assert archive.can_remove(file_path)


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
