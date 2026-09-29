# -*- mode: python -*-
import argparse
import json
import logging
import os
from base64 import b64encode
from pathlib import Path

import httpx
import pytest
import respx

from nbank import archive, core, registry, script, util
from test.test_registry import (
    archives_url,
    base_url,
    bulk_url,
    datatypes_url,
    resource_url,
)

archive_name = "archive"
auth = ("dmeliza", "dummy_pw!")
auth_enc = b64encode(("{}:{}".format(*auth)).encode()).decode()


def random_string(N):
    import random
    import string

    return "".join(
        random.SystemRandom().choice(string.ascii_uppercase + string.digits)
        for _ in range(N)
    )


@pytest.fixture
def tmp_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, base_url, umask=0o027, require_hash=False)


@pytest.fixture
def mocked_api():
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as respx_mock:
        yield respx_mock


@pytest.fixture
def netrc_auth(tmp_path):
    auth_str = "machine localhost\nlogin {}\npassword {}\n".format(*auth)
    netrc = tmp_path / ".netrc"
    netrc.write_text(auth_str)
    return httpx.NetRCAuth(netrc)


def test_deposit_resource(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    metadata = {"experimenter": "dmeliza"}
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    mocked_api.get(datatypes_url).respond(
        json=[{"name": dtype, "content_type": "application/json"}]
    )
    mocked_api.post(
        resource_url,
        json={
            "name": name,
            "dtype": dtype,
            "locations": [archive_name],
            "sha1": sha1,
            "metadata": metadata,
        },
        headers={"Authorization": f"Basic {auth_enc}"},
    ).respond(json={"name": name})
    items = list(
        core.deposit(root, files=[src], dtype=dtype, auth=auth, hash=True, **metadata)
    )
    assert items == [{"source": src, "id": name}]


def test_deposit_dry_run(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    src = tmp_path / name
    src.write_text('{"foo": 10}\n')
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    mocked_api.get(datatypes_url).respond(
        json=[{"name": dtype, "content_type": "application/json"}]
    )
    # no resource POST is mocked: dry_run must not attempt one
    items = list(core.deposit(root, files=[src], dtype=dtype, dry_run=True))
    assert items == [{"source": src, "id": name, "dry_run": True}]
    assert src.exists()  # source is untouched
    assert not archive.resource_path(tmp_archive, name).exists()  # nothing stored


def test_deposit_resource_unknown_dtype(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    dtype = "no-such-dtype"
    src = tmp_path / "dummy"
    src.write_text("blah")
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    mocked_api.get(datatypes_url).respond(
        json=[{"name": "other-dtype", "content_type": "text/plain"}]
    )
    with pytest.raises(RuntimeError, match=dtype):
        _ = list(core.deposit(root, files=[src], dtype=dtype))


@pytest.mark.skip(reason="not implemented")
def test_deposit_uuid_resource():
    # TO DO: verify that deposit assigns resources a valid UUID
    # import uuid
    # uuid.UUID(id)
    pass


@pytest.mark.skip(reason="not implemented")
def test_deposit_directory_resource():
    pass


def test_deposit_resource_archive_errors(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    dtype = "dummy-dtype"
    src = tmp_path / "dummy"
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[])
    # invalid archive
    with pytest.raises(ValueError):
        _ = list(core.deposit(tmp_path, files=[src], dtype=dtype))

    # archive not in registry
    with pytest.raises(RuntimeError):
        _ = list(core.deposit(root, files=[src], dtype=dtype))


def test_deposit_resource_source_errors(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    src = tmp_path / name
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(
        json=[{"name": archive_name, "root": str(root)}],
    )
    mocked_api.get(datatypes_url).respond(
        json=[{"name": dtype, "content_type": "application/json"}]
    )
    # src does not exist
    items = list(core.deposit(root, files=[src], dtype=dtype))
    assert items == []

    # directories are skipped
    items = list(core.deposit(root, files=[tmp_path], dtype=dtype))
    assert items == []

    contents = '{"foo": 10}\n'
    src.write_text(contents)
    src.chmod(0o000)

    # src is not readable
    with pytest.raises(PermissionError, match=str(src)):
        _ = list(core.deposit(root, files=[src], dtype=dtype))

    # tgt is not writable
    src.chmod(0o400)
    tgt_dir = archive.resource_path(tmp_archive, name).parent
    tgt_dir.mkdir(0o444, parents=True)
    with pytest.raises(PermissionError, match=str(tgt_dir)):
        _ = list(core.deposit(root, files=[src], dtype=dtype))


@pytest.mark.parametrize("dry_run", [False, True])
def test_deposit_rejects_symlinks(mocked_api, tmp_path, dry_run):
    root = tmp_path / "archive"
    archive.create(root, base_url, require_hash=False, allow_directories=True)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    real = tmp_path / "real"
    real.write_text("contents")
    link = tmp_path / "dummy_1"
    link.symlink_to(real)
    src_dir = tmp_path / "dummy_2"
    src_dir.mkdir()
    (src_dir / "link").symlink_to(real)
    # no resource POST is mocked: nothing may be registered
    for src, bad in ((link, link), (src_dir, src_dir / "link")):
        with pytest.raises(ValueError, match=str(bad)):
            _ = list(core.deposit(root, files=[src], dry_run=dry_run))
        assert src.exists()
    assert list((root / "resources").iterdir()) == []


def test_store_resources_reports_clean_error(monkeypatch, caplog):
    # deposit errors (PermissionError, RuntimeError, ValueError) should produce
    # a log message, not an uncaught traceback, at the CLI level
    def raise_permission_error(*args, **kwargs):
        raise PermissionError("'/some/path' is not readable")

    monkeypatch.setattr(core, "deposit", raise_permission_error)
    args = argparse.Namespace(
        read_stdin=False,
        file=[Path("dummy")],
        directory=Path("archive"),
        dtype=None,
        hash=False,
        auto_id=False,
        auth=None,
        dry_run=False,
        metadata={},
        json_out=False,
    )
    with caplog.at_level(logging.ERROR, logger="nbank"):
        script.store_resources(args)
    assert "is not readable" in caplog.text


def test_store_resources_passes_dry_run(monkeypatch):
    captured = {}

    def fake_deposit(*args, **kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(core, "deposit", fake_deposit)
    args = argparse.Namespace(
        read_stdin=False,
        file=[Path("dummy")],
        directory=Path("archive"),
        dtype=None,
        hash=False,
        auto_id=False,
        auth=None,
        dry_run=True,
        metadata={},
        json_out=False,
    )
    script.store_resources(args)
    assert captured["dry_run"] is True


def test_deposit_resource_registry_duplicate(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(
        json=[{"name": archive_name, "root": str(root)}],
    )
    mocked_api.get(datatypes_url).respond(
        json=[{"name": dtype, "content_type": "application/json"}]
    )
    # registry will respond with 400 if the resource cannot be created for some
    # reason (duplicate/invalid name, duplicate/invalid sha1, invalid dtype)
    mocked_api.post(
        resource_url,
        json={
            "name": name,
            "dtype": dtype,
            "locations": [archive_name],
            "metadata": {},
        },
    ).respond(
        400,
        json={"error": "something was not valid"},
    )
    with pytest.raises(httpx.HTTPStatusError):
        _ = list(core.deposit(root, files=[src], dtype=dtype, hash=False))


def test_describe_resource(mocked_api):
    name = "dummy_2"
    data = {"you": "found me"}
    mocked_api.get(registry.full_url(base_url, name)).respond(json=data)
    info = core.describe(base_url, name)
    assert info == data


def test_describe_nonexistent_resource(mocked_api):
    name = "dummy_2"
    data = {"detail": "not found"}
    mocked_api.get(registry.full_url(base_url, name)).respond(404, json=data)
    info = core.describe(base_url, name)
    assert info is None


def test_describe_multiple_resources(mocked_api):
    names = ["dummy_2", "dummy_3"]
    data = [{"name": "dummy_2"}, {"name": "dummy_3"}]
    stream = (json.dumps(item).encode() + b"\n" for item in data)
    mocked_api.post(
        registry.url_join(bulk_url, "resources/"), json={"names": names}
    ).respond(stream=stream)
    info = list(core.describe_many(base_url, *names))
    assert info == data


def test_search_resource(mocked_api):
    data = [{"super": "great!"}, {"also": "awesome!"}]
    query = {"sha1": "abc23"}
    mocked_api.get(resource_url, params=query).respond(json=data)
    items = list(core.search(base_url, **query))
    assert items == data


def test_search_nonexistent_resource(mocked_api):
    data = []
    query = {"sha1": "abc23a"}
    mocked_api.get(resource_url, params=query).respond(json=data)
    items = list(core.search(base_url, **query))
    assert items == data


def test_find_resource_location(mocked_api):
    name = "dummy_3"
    mocked_api.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/")
    ).respond(
        json=[
            {
                "scheme": "https",
                "root": "localhost:8000/bucket/",
                "resource_name": name,
            }
        ],
    )
    resource = core.get(base_url, name)
    assert resource.url == f"https://localhost:8000/bucket/{name}/"


def test_find_resource_location_nonexistent(mocked_api):
    name = "dummy_4"
    mocked_api.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/")
    ).respond(json=[])
    item = core.get(base_url, name)
    assert item is None


def test_verify_resource_by_hash(mocked_api, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = [{"sha1": sha1}]
    query = {"sha1": sha1}
    mocked_api.get(resource_url, params=query).respond(json=data)
    items = list(core.verify(base_url, src))
    assert items == data


def test_verify_resource_by_id(mocked_api, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = {"sha1": sha1}
    mocked_api.get(registry.full_url(base_url, name)).respond(json=data)
    assert core.verify(base_url, src, name)


def test_update_metadata(mocked_api):
    name = "dummy_11"
    metadata = {"new": "value"}
    mocked_api.patch(
        registry.full_url(base_url, name),
        json={"metadata": metadata},
        headers={"Authorization": f"Basic {auth_enc}"},
    ).respond(
        json={"name": name, "metadata": metadata},
    )
    updated = list(core.update(base_url, name, auth=auth, **metadata))
    assert updated == [{"metadata": metadata, "name": name}]


def test_update_with_netrc(mocked_api, netrc_auth):
    name = "dummy_11"
    metadata = {"new": "value"}
    mocked_api.patch(
        registry.full_url(base_url, name),
        json={"metadata": metadata},
        headers={"Authorization": f"Basic {auth_enc}"},
    ).respond(
        json={"name": name, "metadata": metadata},
    )
    updated = list(core.update(base_url, name, auth=netrc_auth, **metadata))
    assert updated == [{"metadata": metadata, "name": name}]


def test_check_all_passes(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    src = tmp_path / "res_1"
    src.write_text("contents")
    sha1 = util.hash(src)
    archive.store_resource(tmp_archive, src)
    mocked_api.get(resource_url, params={"has_location": "false"}).respond(json=[])
    mocked_api.get(archives_url).respond(
        json=[
            {"name": archive_name, "scheme": "neurobank", "root": str(root)},
            {"name": "tape", "scheme": "tape", "root": "tape:1"},
        ]
    )
    mocked_api.get(resource_url, params={"location": archive_name}).respond(
        json=[{"name": "res_1", "sha1": sha1, "locations": [archive_name]}]
    )
    mocked_api.get(resource_url, params={"location": "tape"}).respond(
        json=[{"name": "res_2", "sha1": None, "locations": ["tape"]}]
    )
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    try:
        assert script.main(["-r", base_url, "check", "all"]) == 0
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)


def run_main(*argv):
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    try:
        return script.main(["-r", base_url, *argv])
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)


def test_init_shared_archive(mocked_api, tmp_path):
    import grp

    group = grp.getgrgid(os.getgid()).gr_name
    root = tmp_path / "archive"
    mocked_api.post(archives_url).respond(201, json={})
    run_main("init", "--shared", "-g", group, str(root))
    access = archive.get_config(root)["policy"]["access"]
    assert access["user"] is None
    assert access["group"] == group


def test_init_unknown_group(mocked_api, tmp_path, caplog):
    # no registry route is mocked: the archive must not be registered
    root = tmp_path / "archive"
    run_main("init", "-g", "no-such-group-xyzzy", str(root))
    assert "group 'no-such-group-xyzzy' does not exist" in caplog.text
    assert not root.exists()
