# -*- mode: python -*-
import grp
import os
import pwd

import pytest
import respx

from nbank import archive, check, util
from nbank.check import Status
from test.test_registry import base_url, resource_url


@pytest.fixture
def tmp_archive(tmp_path):
    return archive.create(tmp_path / "archive", base_url, umask=0o027)


def store(cfg, tmp_path, name, contents="contents"):
    src = tmp_path / f"{name}.txt"
    src.write_text(contents)
    sha1 = util.hash(src)
    return archive.store_resource(cfg, src, id=name), sha1


def statuses(findings):
    return {f.resource: f.status for f in findings}


def test_check_contents_ok(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert findings == [check.Finding(Status.OK, "res_1", path)]
    assert findings[0].ok


def test_check_contents_not_hashed(tmp_archive, tmp_path):
    store(tmp_archive, tmp_path, "res_1")
    findings = check.check_archive_contents(tmp_archive["path"], {"res_1": None})
    assert statuses(findings) == {"res_1": Status.NOT_HASHED}


def test_check_contents_hash_mismatch(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    path.write_text("changed")
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert statuses(findings) == {"res_1": Status.HASH_MISMATCH}
    assert not findings[0].ok


def test_check_contents_skip_hash(tmp_archive, tmp_path, monkeypatch):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    path.write_text("changed")

    def no_hashing(*args, **kwargs):
        raise AssertionError("contents should not be read")

    monkeypatch.setattr(util, "hash", no_hashing)
    findings = list(
        check.check_archive_contents(
            tmp_archive["path"], {"res_1": sha1}, check_hash=False
        )
    )
    assert findings == [check.Finding(Status.HASH_SKIPPED, "res_1", path)]
    assert findings[0].ok


def test_check_contents_unreadable(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    path.chmod(0o000)
    try:
        findings = check.check_archive_contents(tmp_archive["path"], {"res_1": sha1})
        assert statuses(findings) == {"res_1": Status.UNREADABLE}
    finally:
        path.chmod(0o600)


def test_check_contents_missing(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "extra")
    findings = list(check.check_archive_contents(tmp_archive["path"], {"absent": None}))
    assert statuses(findings) == {
        "extra": Status.MISSING_FROM_REGISTRY,
        "absent": Status.MISSING_FROM_ARCHIVE,
    }
    assert check.Finding(Status.MISSING_FROM_REGISTRY, "extra", path) in findings


def test_check_contents_unexpected_file(tmp_archive, tmp_path):
    stray = tmp_archive["path"] / "resources" / ".DS_Store"
    stray.write_text("junk")
    findings = list(check.check_archive_contents(tmp_archive["path"], {}))
    assert findings == [check.Finding(Status.UNEXPECTED, ".DS_Store", stray)]


def test_check_contents_misplaced(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    wrong_dir = tmp_archive["path"] / "resources" / "zz"
    wrong_dir.mkdir()
    moved = path.rename(wrong_dir / path.name)
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert findings == [check.Finding(Status.MISPLACED, "res_1", moved)]


def test_check_contents_duplicate(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    other = path.with_suffix(".json")
    other.write_text("{}")
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert sorted(f.path for f in findings) == sorted([path, other])
    assert all(f.status == Status.DUPLICATE for f in findings)


@respx.mock(assert_all_called=True, assert_all_mocked=True)
def test_registry_resources_in_archive(respx_mock):
    import httpx

    respx_mock.get(resource_url, params={"location": "my-archive"}).respond(
        json=[
            {"name": "res_1", "sha1": "abc", "locations": ["my-archive"]},
            {"name": "res_2", "sha1": None, "locations": ["other", "my-archive"]},
            # the registry's location filter matches substrings
            {"name": "res_3", "sha1": None, "locations": ["my-archive-copy"]},
        ]
    )
    with httpx.Client() as session:
        expected = check.registry_resources_in_archive(session, base_url, "my-archive")
    assert expected == {"res_1": "abc", "res_2": None}


def test_check_contents_unlistable_subdirectory(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    path.parent.chmod(0o000)
    try:
        findings = list(
            check.check_archive_contents(tmp_archive["path"], {"res_1": sha1})
        )
    finally:
        path.parent.chmod(0o750)
    assert statuses(findings) == {
        None: Status.UNREADABLE,
        "res_1": Status.MISSING_FROM_ARCHIVE,
    }


def permission_findings(cfg):
    return [(f.status, f.path) for f in check.check_archive_permissions(cfg)]


def test_check_permissions_ok(tmp_archive, tmp_path):
    store(tmp_archive, tmp_path, "res_1")
    assert permission_findings(tmp_archive) == []


def test_check_permissions_file_mode(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "res_1")
    # umask is 027: group read is required, other read is forbidden
    path.chmod(0o604)
    findings = list(check.check_archive_permissions(tmp_archive))
    assert [(f.status, f.path) for f in findings] == [(Status.WRONG_MODE, path)]
    assert findings[0].resource == "res_1"
    assert "missing 0040" in findings[0].detail
    assert "has 0004 forbidden by umask" in findings[0].detail


def test_check_permissions_subdirectory_mode(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "res_1")
    # group members can't deposit here
    path.parent.chmod(0o700)
    findings = list(check.check_archive_permissions(tmp_archive))
    assert [(f.status, f.path, f.resource) for f in findings] == [
        (Status.WRONG_MODE, path.parent, None)
    ]
    assert "missing" in findings[0].detail


def test_check_permissions_directory_resource(tmp_path):
    cfg = archive.create(
        tmp_path / "archive", base_url, umask=0o027, allow_directories=True
    )
    src = tmp_path / "res_1"
    src.mkdir()
    (src / "data").write_text("contents")
    path = archive.store_resource(cfg, src)
    (path / "data").chmod(0o600)
    (path / "link").symlink_to(path / "data")
    assert permission_findings(cfg) == [(Status.WRONG_MODE, path / "data")]


@pytest.mark.skipif(os.getuid() == 0, reason="root can own anything")
def test_check_permissions_owner_and_group(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "res_1")
    root = tmp_archive["path"] / "resources"
    tmp_archive["policy"]["access"]["user"] = pwd.getpwuid(0).pw_name
    tmp_archive["policy"]["access"]["group"] = grp.getgrgid(0).gr_name
    findings = permission_findings(tmp_archive)
    for p in (root, path.parent, path):
        assert (Status.WRONG_OWNER, p) in findings
        if os.getgid() != 0:
            assert (Status.WRONG_GROUP, p) in findings


def test_check_permissions_unknown_user(tmp_archive):
    tmp_archive["policy"]["access"]["user"] = "no-such-user-xyzzy"
    with pytest.raises(ValueError, match="no-such-user-xyzzy"):
        list(check.check_archive_permissions(tmp_archive))


def test_check_contents_symlinked_resource(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    real = tmp_path / "elsewhere.txt"
    path.rename(real)
    path.symlink_to(real)
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert findings == [check.Finding(Status.SYMLINK, "res_1", path)]
    assert permission_findings(tmp_archive) == []


def test_check_contents_symlinked_subdirectory(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    real = tmp_path / "elsewhere"
    path.parent.rename(real)
    path.parent.symlink_to(real)
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert findings == [
        check.Finding(Status.SYMLINK, None, path.parent),
        check.Finding(Status.OK, "res_1", path),
    ]
    assert permission_findings(tmp_archive) == []


def test_check_contents_symlink_in_directory_resource(tmp_path):
    cfg = archive.create(
        tmp_path / "archive", base_url, umask=0o027, allow_directories=True
    )
    src = tmp_path / "res_1"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("contents")
    (src / "sub" / "link").symlink_to(src / "sub" / "data")
    (src / "dirlink").symlink_to(src / "sub")
    path = archive.store_resource(cfg, src)
    findings = list(check.check_archive_contents(cfg["path"], {"res_1": None}))
    assert findings == [
        check.Finding(Status.NOT_HASHED, "res_1", path),
        check.Finding(Status.SYMLINK, "res_1", path / "dirlink"),
        check.Finding(Status.SYMLINK, "res_1", path / "sub" / "link"),
    ]


def test_fix_permissions(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "res_1")
    path.chmod(0o604)
    path.parent.chmod(0o700)
    findings = list(check.check_archive_permissions(tmp_archive, fix=True))
    assert [(f.status, f.path, f.fixed) for f in findings] == [
        (Status.WRONG_MODE, path.parent, True),
        (Status.WRONG_MODE, path, True),
    ]
    assert all(f.ok for f in findings)
    assert permission_findings(tmp_archive) == []


def test_fix_permissions_reaches_unlistable_directories(tmp_path):
    cfg = archive.create(
        tmp_path / "archive", base_url, umask=0o027, allow_directories=True
    )
    src = tmp_path / "res_1"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("contents")
    path = archive.store_resource(cfg, src)
    (path / "sub" / "data").chmod(0o600)
    (path / "sub").chmod(0o000)
    try:
        # without fixing, the unlistable directory hides its contents
        assert permission_findings(cfg) == [(Status.WRONG_MODE, path / "sub")]
        findings = list(check.check_archive_permissions(cfg, fix=True))
    finally:
        (path / "sub").chmod(0o750)
    assert [(f.status, f.path, f.fixed) for f in findings] == [
        (Status.WRONG_MODE, path / "sub", True),
        (Status.WRONG_MODE, path / "sub" / "data", True),
    ]


@pytest.mark.skipif(os.getuid() == 0, reason="root can change ownership")
def test_fix_permissions_without_root(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "res_1")
    path.chmod(0o604)
    tmp_archive["policy"]["access"]["user"] = pwd.getpwuid(0).pw_name
    tmp_archive["policy"]["access"]["group"] = grp.getgrgid(0).gr_name
    findings = [
        (f.status, f.fixed)
        for f in check.check_archive_permissions(tmp_archive, fix=True)
        if f.path == path
    ]
    # ownership can't be changed, but the mode still can
    assert (Status.WRONG_OWNER, False) in findings
    assert (Status.WRONG_MODE, True) in findings
    if os.getgid() != 0 and 0 not in os.getgroups():
        assert (Status.WRONG_GROUP, False) in findings
