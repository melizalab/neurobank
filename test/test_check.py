# -*- mode: python -*-
import grp
import json
import os
import pwd
import time

import pytest
import respx

from nbank import archive, check, util
from nbank.check import Status
from test.test_registry import (
    archives_url,
    base_url,
    bulk_url,
    info_url,
    resource_url,
)


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
    path.chmod(0o640)
    path.write_text("changed")
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    assert statuses(findings) == {"res_1": Status.HASH_MISMATCH}
    assert not findings[0].ok


def test_check_contents_skip_hash(tmp_archive, tmp_path, monkeypatch):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    path.chmod(0o640)
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
def test_registry_resources_in_archive_before_api_1_1(respx_mock):
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
        expected = check.registry_resources_in_archive(
            session, base_url, "my-archive", api_version=(1, 0)
        )
    assert expected == {"res_1": "abc", "res_2": None}


@respx.mock(assert_all_called=True, assert_all_mocked=True)
def test_registry_resources_in_archive_looks_up_version(respx_mock):
    import httpx

    respx_mock.get(info_url).respond(json={"api_version": "1.1"})
    respx_mock.get(resource_url, params={"archive": "my-archive"}).respond(
        json=[{"name": "res_1", "sha1": "abc", "locations": ["my-archive"]}]
    )
    with httpx.Client() as session:
        expected = check.registry_resources_in_archive(session, base_url, "my-archive")
    assert expected == {"res_1": "abc"}


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
    # umask is 027 and resources are read-only: group read is required, and
    # other read and all write bits are forbidden
    path.chmod(0o604)
    findings = list(check.check_archive_permissions(tmp_archive))
    assert [(f.status, f.path) for f in findings] == [(Status.WRONG_MODE, path)]
    assert findings[0].resource == "res_1"
    assert "missing 0040" in findings[0].detail
    assert "has 0204, which the policy forbids" in findings[0].detail


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
    (src / "link").symlink_to(src / "data")
    path = archive.store_resource(cfg, src)
    (path / "data").chmod(0o600)
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


def resource_record(name, locations, sha1=None):
    return {"name": name, "sha1": sha1, "locations": locations}


@pytest.fixture
def mocked_api():
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as respx_mock:
        yield respx_mock


def test_resources_without_locations(mocked_api):
    import httpx

    mocked_api.get(resource_url, params={"has_location": "false"}).respond(
        json=[resource_record("res_1", []), resource_record("res_2", [])]
    )
    with httpx.Client() as session:
        names = list(check.resources_without_locations(session, base_url))
    assert names == ["res_1", "res_2"]


def test_resources_without_locations_unsupported(mocked_api):
    import httpx

    # an older registry ignores the filter and returns everything
    mocked_api.get(resource_url, params={"has_location": "false"}).respond(
        json=[resource_record("res_1", []), resource_record("res_2", ["archive"])]
    )
    with httpx.Client() as session, pytest.raises(RuntimeError, match="has_location"):
        list(check.resources_without_locations(session, base_url))


@respx.mock(assert_all_called=False, assert_all_mocked=True)
def test_archive_has_resources_stops_at_first_match(respx_mock):
    import httpx

    page_2 = respx_mock.get(resource_url, params={"location": "arch", "page": "2"})
    respx_mock.get(resource_url, params={"location": "arch"}).respond(
        json=[resource_record("res_1", ["arch"])],
        headers={"Link": f'<{resource_url}?location=arch&page=2>; rel="next"'},
    )
    with httpx.Client() as session:
        assert check.archive_has_resources(session, base_url, "arch", (1, 0))
    assert not page_2.called


def test_archive_has_resources_exact_match(mocked_api):
    import httpx

    # before API version 1.1, the registry's location filter matches substrings
    mocked_api.get(resource_url, params={"location": "arch"}).respond(
        json=[resource_record("res_1", ["arch-copy"])]
    )
    with httpx.Client() as session:
        assert not check.archive_has_resources(session, base_url, "arch", (1, 0))


def test_archive_has_resources_exact_filter(mocked_api):
    import httpx

    mocked_api.get(resource_url, params={"archive": "arch"}).respond(
        json=[resource_record("res_1", ["arch"])]
    )
    with httpx.Client() as session:
        assert check.archive_has_resources(session, base_url, "arch", (1, 1))


def test_check_registry(mocked_api):
    import httpx

    mocked_api.get(resource_url, params={"has_location": "false"}).respond(
        json=[resource_record("orphan", [])]
    )
    mocked_api.get(info_url).respond(json={"api_version": "1.1"})
    mocked_api.get(archives_url).respond(json=[{"name": "full"}, {"name": "empty"}])
    mocked_api.get(resource_url, params={"archive": "full"}).respond(
        json=[resource_record("res_1", ["full"])]
    )
    mocked_api.get(resource_url, params={"archive": "empty"}).respond(json=[])
    with httpx.Client() as session:
        findings = list(check.check_registry(session, base_url))
    assert findings == [
        check.Finding(Status.NO_LOCATION, "orphan"),
        check.Finding(Status.EMPTY_ARCHIVE, None, archive="empty"),
    ]
    assert [f.ok for f in findings] == [False, True]


def test_check_contents_unlistable_resources_directory(tmp_archive, tmp_path):
    _, sha1 = store(tmp_archive, tmp_path, "res_1")
    base = tmp_archive["path"] / "resources"
    base.chmod(0o000)
    try:
        findings = list(
            check.check_archive_contents(tmp_archive["path"], {"res_1": sha1})
        )
    finally:
        base.chmod(0o750)
    assert statuses(findings) == {
        None: Status.UNREADABLE,
        "res_1": Status.MISSING_FROM_ARCHIVE,
    }
    assert findings[0].path == base


def test_check_missing_resources_directory(tmp_archive):
    base = tmp_archive["path"] / "resources"
    base.rmdir()
    findings = list(check.check_archive_contents(tmp_archive["path"], {}))
    assert [(f.status, f.path) for f in findings] == [(Status.UNREADABLE, base)]
    assert "No such file" in findings[0].detail
    assert permission_findings(tmp_archive) == []


def bulk_response(*records):
    return (json.dumps(record).encode() + b"\n" for record in records)


def test_recheck_unregistered(mocked_api, tmp_archive, tmp_path):
    import httpx

    copy, sha1 = store(tmp_archive, tmp_path, "copy")
    store(tmp_archive, tmp_path, "changed")
    store(tmp_archive, tmp_path, "unhashed")
    unknown, _ = store(tmp_archive, tmp_path, "unknown")
    findings = list(check.check_archive_contents(tmp_archive["path"], {}))
    route = mocked_api.post(bulk_url + "resources/").respond(
        stream=bulk_response(
            resource_record("copy", ["other"], sha1),
            resource_record("changed", ["other"], "0" * 40),
            resource_record("unhashed", [], None),
        )
    )
    with httpx.Client() as session:
        rechecked = list(check.recheck_unregistered(session, base_url, findings))
    assert json.loads(route.calls.last.request.content) == {
        "names": ["changed", "copy", "unhashed", "unknown"]
    }
    by_name = {f.resource: f for f in rechecked}
    assert by_name["copy"] == check.Finding(
        Status.REGISTERED_ELSEWHERE,
        "copy",
        copy,
        "located in: other",
        locations=("other",),
    )
    assert by_name["changed"].status == Status.CHANGED_ELSEWHERE
    assert by_name["changed"].locations == ("other",)
    assert by_name["unhashed"].status == Status.UNVERIFIED_ELSEWHERE
    assert by_name["unhashed"].locations == ()
    assert by_name["unhashed"].detail == "the registry lists no locations"
    assert by_name["unknown"] == check.Finding(
        Status.MISSING_FROM_REGISTRY, "unknown", unknown
    )
    assert not any(f.ok for f in rechecked)


def test_recheck_unregistered_nothing_to_look_up(mocked_api, tmp_archive, tmp_path):
    import httpx

    findings = [check.Finding(Status.MISSING_FROM_ARCHIVE, "res_1")]
    with httpx.Client() as session:
        assert list(check.recheck_unregistered(session, base_url, findings)) == findings


def test_check_contents_incomplete_transfers(tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    stub = path.parent
    leftover_file = stub / ".res_2.wav.partial"
    leftover_file.write_text("half")
    leftover_dir = stub / ".res_3.partial"
    (leftover_dir / "sub").mkdir(parents=True)
    three_days_ago = time.time() - 3 * 86400 - 60
    os.utime(leftover_file, (three_days_ago, three_days_ago))
    findings = list(check.check_archive_contents(tmp_archive["path"], {"res_1": sha1}))
    incomplete = {f.path: f for f in findings if f.status == Status.INCOMPLETE}
    assert set(incomplete) == {leftover_file, leftover_dir}
    assert incomplete[leftover_file].resource == "res_2"
    assert incomplete[leftover_file].detail == "last modified 3 days ago"
    assert incomplete[leftover_dir].resource == "res_3"
    assert not any(f.ok for f in incomplete.values())
    assert statuses(f for f in findings if f.status != Status.INCOMPLETE) == {
        "res_1": Status.OK
    }


def test_check_permissions_skips_incomplete_transfers(tmp_archive, tmp_path):
    path, _ = store(tmp_archive, tmp_path, "res_1")
    leftover = path.parent / ".res_2.partial"
    leftover.write_text("half")
    leftover.chmod(0o666)
    assert permission_findings(tmp_archive) == []
