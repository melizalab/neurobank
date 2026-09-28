# -*- mode: python -*-
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
