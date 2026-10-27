# -*- mode: python -*-
import argparse
import io
import json
import logging
import os
import re
from base64 import b64encode
from pathlib import Path

import httpx
import pytest
import respx

from nbank import archive, canonical_zip, check, core, registry, script, util
from test import mock_archive_locations, skip_unless_posix
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
def netrc_auth(tmp_path):
    auth_str = "machine localhost\nlogin {}\npassword {}\n".format(*auth)
    netrc = tmp_path / ".netrc"
    netrc.write_text(auth_str)
    return httpx.NetRCAuth(netrc)


def test_deposit_resource(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
    name = "dummy_1"
    dtype = "dummy-dtype"
    metadata = {"experimenter": "dmeliza", "pen": 1}
    src = tmp_path / f"{name}.json"
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
            "locations": [{"archive": archive_name, "key": f"{name}.json"}],
            "sha1": sha1,
            "experimenter": "dmeliza",
            "metadata": {"pen": 1},
        },
        headers={"Authorization": f"Basic {auth_enc}"},
    ).respond(json={"name": name})
    items = list(
        core.deposit(
            root, files=[src], dtype=dtype, auth=auth, hash=True, metadata=metadata
        )
    )
    assert items == [{"source": src, "id": name}]
    assert archive.resource_path(tmp_archive, f"{name}.json").exists()


def test_deposit_dry_run(mocked_api, tmp_archive, tmp_path, registry_checks):
    registry_checks()
    root = tmp_archive.path
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
    assert items == [{"source": src, "id": name, "dry_run": True, "metadata": {}}]
    assert src.exists()  # source is untouched
    assert not archive.resource_path(tmp_archive, name).exists()  # nothing stored


def dry_run_setup(mocked_api, tmp_archive, tmp_path, names):
    """Mocks the archive and dtype lookups; returns the files to deposit."""
    root = tmp_archive.path
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    mocked_api.get(datatypes_url).respond(
        json=[{"name": "dummy-dtype", "content_type": "application/json"}]
    )
    files = []
    for name in names:
        src = tmp_path / name
        src.write_text(name)
        files.append(src)
    return files


def test_deposit_dry_run_asks_the_registry(mocked_api, tmp_archive, tmp_path):
    files = dry_run_setup(mocked_api, tmp_archive, tmp_path, ["res_1", "res_2"])
    route = mocked_api.post(f"{base_url}check/resources/").respond(
        json=[{}, {"name": ["resource with this name already exists."]}]
    )
    items = list(
        core.deposit(
            tmp_archive.path,
            files=files,
            dtype="dummy-dtype",
            hash=True,
            dry_run=True,
            skip_errors=True,
            metadata={"experimenter": "dmeliza", "pen": 1},
        )
    )
    assert json.loads(route.calls.last.request.content) == [
        {
            "name": src.name,
            "dtype": "dummy-dtype",
            "locations": [{"archive": archive_name, "key": src.name}],
            "sha1": util.hash(src),
            "experimenter": "dmeliza",
            "metadata": {"pen": 1},
        }
        for src in files
    ]
    assert items[0]["id"] == "res_1"
    assert items[1] == {
        "source": files[1],
        "error": "name: resource with this name already exists.",
    }


def test_deposit_dry_run_refusal_raises(mocked_api, tmp_archive, tmp_path):
    files = dry_run_setup(mocked_api, tmp_archive, tmp_path, ["res_1"])
    mocked_api.post(f"{base_url}check/resources/").respond(
        json=[{"experimenter": ["no such user"]}]
    )
    with pytest.raises(ValueError, match="registry would refuse it: experimenter"):
        list(
            core.deposit(
                tmp_archive.path, files=files, dtype="dummy-dtype", dry_run=True
            )
        )


def test_deposit_dry_run_checks_in_batches(
    mocked_api, tmp_archive, tmp_path, registry_checks, monkeypatch
):
    monkeypatch.setattr(util, "check_batch_size", 2)
    route = registry_checks()
    files = dry_run_setup(mocked_api, tmp_archive, tmp_path, ["a", "b", "c"])
    items = list(
        core.deposit(tmp_archive.path, files=files, dtype="dummy-dtype", dry_run=True)
    )
    assert [item["id"] for item in items] == ["a", "b", "c"]
    assert [len(json.loads(c.request.content)) for c in route.calls] == [2, 1]


def test_deposit_resource_unknown_dtype(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
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


@pytest.mark.posix
@pytest.mark.parametrize("auto_id_type", [None, "base36"])
def test_deposit_auto_id(mocked_api, tmp_path, auto_id_type):
    root = tmp_path / "archive"
    archive.create(
        root,
        base_url,
        require_hash=False,
        auto_identifiers=True,
        auto_id_type=auto_id_type,
    )
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    route = mocked_api.post(resource_url).mock(
        side_effect=lambda request: httpx.Response(
            201, json={"name": json.loads(request.content)["name"]}
        )
    )
    src = tmp_path / "dummy_1.wav"
    src.write_text("contents")
    [item] = core.deposit(root, files=[src], auth=auth)
    id = item["id"]
    assert re.fullmatch(r"[0-9a-z]{8}", id)
    body = json.loads(route.calls.last.request.content)
    assert body["name"] == id
    # the id is known before registering, so the key can go with it
    assert body["locations"] == [{"archive": archive_name, "key": f"{id}.wav"}]
    assert archive.resource_path(root, f"{id}.wav").exists()


@pytest.fixture
def auto_id_archive(mocked_api, tmp_path):
    skip_unless_posix()
    root = tmp_path / "archive"
    archive.create(root, base_url, require_hash=False, auto_identifiers=True)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    return root


name_taken = {"name": ["resource with this name already exists."]}


def test_deposit_auto_id_taken_tries_another(mocked_api, auto_id_archive, tmp_path):
    responses = iter([httpx.Response(400, json=name_taken)])

    def respond(request):
        name = json.loads(request.content)["name"]
        return next(responses, httpx.Response(201, json={"name": name}))

    route = mocked_api.post(resource_url).mock(side_effect=respond)
    src = tmp_path / "dummy_1.wav"
    src.write_text("contents")
    [item] = core.deposit(auto_id_archive, files=[src], auth=auth)
    first, second = (json.loads(call.request.content) for call in route.calls)
    assert first["name"] != second["name"] == item["id"]
    assert second["locations"] == [
        {"archive": archive_name, "key": f"{item['id']}.wav"}
    ]
    assert archive.resource_path(auto_id_archive, f"{item['id']}.wav").exists()


def test_deposit_auto_id_taken_gives_up(mocked_api, auto_id_archive, tmp_path):
    route = mocked_api.post(resource_url).respond(400, json=name_taken)
    src = tmp_path / "dummy_1.wav"
    src.write_text("contents")
    [item] = core.deposit(auto_id_archive, files=[src], auth=auth, skip_errors=True)
    assert len(route.calls) == 3
    assert item == {"source": src, "error": f"name: {name_taken['name'][0]}"}
    assert src.exists()


def test_deposit_name_taken_is_not_retried(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    route = mocked_api.post(resource_url).respond(400, json=name_taken)
    src = tmp_path / "dummy_1.wav"
    src.write_text("contents")
    [item] = core.deposit(root, files=[src], auth=auth, skip_errors=True)
    assert len(route.calls) == 1
    assert "error" in item


@pytest.mark.posix
def test_deposit_unknown_auto_id_type(tmp_path):
    root = tmp_path / "archive"
    archive.create(root, base_url, auto_identifiers=True, auto_id_type="base64")
    src = tmp_path / "dummy_1.wav"
    src.write_text("contents")
    with pytest.raises(ValueError, match="auto_id_type 'base64' isn't known"):
        list(core.deposit(root, files=[src], auth=auth))
    assert src.exists()


@pytest.mark.posix
@pytest.mark.parametrize("skip_errors", [False, True])
def test_deposit_refuses_directories(mocked_api, tmp_path, skip_errors, caplog):
    # even when the archive's policy allows directory resources
    root = tmp_path / "archive"
    archive.create(root, base_url, require_hash=False)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    src = tmp_path / "dummy_1"
    src.mkdir()
    (src / "data").write_text("contents")
    caplog.set_level(logging.INFO, logger="nbank")
    # no resource POST is mocked: nothing may be registered
    items = list(core.deposit(root, files=[src], skip_errors=skip_errors))
    if skip_errors:
        assert items == [
            {
                "source": src,
                "error": "is a directory; zip it first, or deposit with "
                "--zip-directories",
            }
        ]
    else:
        assert items == []
        assert "is a directory; zip it first" in caplog.text
    assert (src / "data").exists()
    assert list((root / "resources").iterdir()) == []


@pytest.fixture
def recording(tmp_path):
    """A directory to deposit as a zip, and the zip it should become"""
    src = tmp_path / "rec_1"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.abf").write_bytes(b"x" * 100)
    (src / "notes.txt").write_text("notes")
    expected = canonical_zip.zip_directory(src, tmp_path / "expected.zip")
    return src, expected


@pytest.fixture
def zip_archive(mocked_api, tmp_path):
    skip_unless_posix()
    root = tmp_path / "archive"
    archive.create(root, base_url, require_hash=True)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    return archive.ArchiveConfig.load(root)


def staged_files(cfg):
    return list(archive.staging_dir(cfg).iterdir())


def test_deposit_zips_directories(mocked_api, zip_archive, recording):
    src, expected = recording
    mocked_api.post(
        resource_url,
        json={
            "name": "rec_1",
            "locations": [{"archive": archive_name, "key": "rec_1.zip"}],
            "sha1": util.hash(expected),
            "metadata": {},
        },
    ).respond(json={"name": "rec_1"})
    items = list(core.deposit(zip_archive.path, [src], zip_directories=True, auth=auth))
    assert items == [{"source": src, "id": "rec_1", "zipped": True}]
    stored = archive.resource_path(zip_archive, "rec_1", resolve_ext=True)
    assert stored.name == "rec_1.zip"
    assert stored.read_bytes() == expected.read_bytes()
    # the directory is left in place, and nothing is left staged
    assert (src / "sub" / "a.abf").exists()
    assert staged_files(zip_archive) == []


def test_deposit_zip_dry_run_builds_nothing(
    zip_archive, recording, monkeypatch, registry_checks
):
    registry_checks()
    src, _ = recording

    def fail(*args, **kwargs):
        raise AssertionError("the zip must not be built in a dry run")

    monkeypatch.setattr(canonical_zip, "zip_directory", fail)
    # no resource POST is mocked: nothing may be registered
    items = list(
        core.deposit(zip_archive.path, [src], zip_directories=True, dry_run=True)
    )
    assert items == [
        {"source": src, "id": "rec_1", "dry_run": True, "metadata": {}, "zipped": True}
    ]
    assert list((zip_archive.path / "resources").iterdir()) == []
    assert staged_files(zip_archive) == []


def test_deposit_zip_rejected_by_registry_is_removed(
    mocked_api, zip_archive, recording
):
    src, _ = recording
    mocked_api.post(resource_url).respond(400, json={"name": ["already exists"]})
    [item] = core.deposit(
        zip_archive.path, [src], zip_directories=True, skip_errors=True, auth=auth
    )
    assert "already exists" in item["error"]
    assert list((zip_archive.path / "resources").iterdir()) == []
    assert staged_files(zip_archive) == []


def test_deposit_zip_failure_leaves_nothing(zip_archive, recording):
    src, _ = recording
    (src / "link").symlink_to(src / "notes.txt")
    # no resource POST is mocked: nothing may be registered
    [item] = core.deposit(
        zip_archive.path, [src], zip_directories=True, skip_errors=True
    )
    assert "symbolic link" in item["error"]
    assert list((zip_archive.path / "resources").iterdir()) == []
    assert staged_files(zip_archive) == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_deposit_zip_needs_staging_directory(zip_archive, recording, dry_run):
    src, _ = recording
    archive.staging_dir(zip_archive).rmdir()
    [item] = core.deposit(
        zip_archive.path,
        [src],
        zip_directories=True,
        skip_errors=True,
        dry_run=dry_run,
    )
    assert "no staging directory" in item["error"]
    assert "nbank check archive --fix" in item["error"]


def test_deposit_resource_archive_errors(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
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
    root = tmp_archive.path
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


@pytest.mark.posix
@pytest.mark.parametrize("dry_run", [False, True])
def test_deposit_rejects_symlinks(mocked_api, tmp_path, dry_run):
    root = tmp_path / "archive"
    archive.create(root, base_url, require_hash=False)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    real = tmp_path / "real"
    real.write_text("contents")
    link = tmp_path / "dummy_1"
    link.symlink_to(real)
    # no resource POST is mocked: nothing may be registered
    with pytest.raises(ValueError, match=str(link)):
        _ = list(core.deposit(root, files=[link], dry_run=dry_run))
    assert link.exists()
    assert list((root / "resources").iterdir()) == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_deposit_skip_errors(
    mocked_api, tmp_archive, tmp_path, dry_run, registry_checks
):
    if dry_run:
        registry_checks()
    root = tmp_archive.path
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    missing = tmp_path / "missing"
    directory = tmp_path / "a_directory"
    directory.mkdir()
    bad_name = tmp_path / "not valid"
    bad_name.write_text("contents")
    link = tmp_path / "linked"
    link.symlink_to(bad_name)
    good = tmp_path / "good"
    good.write_text("contents")
    if not dry_run:
        mocked_api.post(resource_url).respond(201, json={"name": "good"})
    sources = [missing, directory, bad_name, link, good]
    items = list(core.deposit(root, sources, skip_errors=True, dry_run=dry_run))
    assert [item["source"] for item in items] == sources
    errors = [item.get("error") for item in items]
    assert errors[0] == "does not exist"
    assert "is a directory" in errors[1]
    assert "contains invalid characters" in errors[2]
    assert "is a symbolic link" in errors[3]
    assert errors[4] is None
    assert items[4]["id"] == "good"
    assert (archive.resource_path(tmp_archive, "good").exists()) != dry_run


def test_deposit_skip_errors_rejected_by_registry(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    rejected, good = tmp_path / "rejected", tmp_path / "good"
    rejected.write_text("contents")
    good.write_text("other contents")
    mocked_api.post(resource_url, json__name="rejected").respond(
        400, json={"name": ["a resource with this name already exists"]}
    )
    mocked_api.post(resource_url, json__name="good").respond(201, json={"name": "good"})
    items = list(core.deposit(root, [rejected, good], skip_errors=True))
    assert items == [
        {
            "source": rejected,
            "error": "name: a resource with this name already exists",
        },
        {"source": good, "id": "good"},
    ]
    assert rejected.exists()
    assert not good.exists()


def test_deposit_skip_errors_still_raises_auth_errors(
    mocked_api, tmp_archive, tmp_path
):
    root = tmp_archive.path
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    src = tmp_path / "res_1"
    src.write_text("contents")
    mocked_api.post(resource_url).respond(403, json={"detail": "not allowed"})
    with pytest.raises(httpx.HTTPStatusError):
        list(core.deposit(root, [src], skip_errors=True))


def test_store_resources_continues_past_errors(monkeypatch, capsys, caplog):
    def fake_deposit(*args, skip_errors=False, **kwargs):
        assert skip_errors
        yield {"source": Path("bad"), "error": "does not exist"}
        yield {"source": Path("good"), "id": "good"}

    monkeypatch.setattr(core, "deposit", fake_deposit)
    args = argparse.Namespace(
        read_stdin=False,
        file=[Path("bad"), Path("good")],
        directory=Path("archive"),
        dtype=None,
        hash=False,
        auto_id=False,
        auth=None,
        dry_run=False,
        metadata={},
        experimenter=None,
        animal=None,
        read_metadata=True,
        override_file_metadata=False,
        zip_directories=False,
        json_out=True,
    )
    assert script.store_resources(args) == 1
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        {"source": "bad", "error": "does not exist"},
        {"source": "good", "id": "good"},
    ]
    assert "could not be deposited: 1" in caplog.text


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
        experimenter=None,
        animal=None,
        read_metadata=True,
        override_file_metadata=False,
        zip_directories=False,
        json_out=False,
    )
    with caplog.at_level(logging.ERROR, logger="nbank"):
        assert script.store_resources(args) == 1
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
        experimenter=None,
        animal=None,
        read_metadata=True,
        override_file_metadata=False,
        zip_directories=False,
        json_out=False,
    )
    script.store_resources(args)
    assert captured["dry_run"] is True


@pytest.mark.posix
@pytest.mark.parametrize(
    "flags, expected",
    [
        (
            (),
            {
                "read_metadata": True,
                "override_file_metadata": False,
                "zip_directories": False,
            },
        ),
        (
            ("--no-file-metadata", "--override-file-metadata", "--zip-directories"),
            {
                "read_metadata": False,
                "override_file_metadata": True,
                "zip_directories": True,
            },
        ),
    ],
)
def test_deposit_passes_file_metadata_options(monkeypatch, flags, expected):
    captured = {}

    def fake_deposit(*args, **kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(core, "deposit", fake_deposit)
    run_main("deposit", *flags, "archive", "file")
    assert {k: captured[k] for k in expected} == expected


@pytest.mark.posix
def test_deposit_metadata_keys_can_share_option_names(monkeypatch):
    captured = {}

    def fake_deposit(*args, **kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(core, "deposit", fake_deposit)
    keys = ("archive", "dtype", "sha1", "id", "auth", "hash", "dry_run")
    args = [arg for key in keys for arg in ("-k", f"{key}=x")]
    assert run_main("deposit", *args, "archive", "file") is None
    assert captured["metadata"] == dict.fromkeys(keys, "x")
    assert captured["dtype"] is None and captured["dry_run"] is False


def test_deposit_resource_registry_duplicate(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
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
            "locations": [{"archive": archive_name, "key": name}],
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


def test_find_by_hash(mocked_api, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = [{"sha1": sha1}]
    query = {"sha1": sha1}
    mocked_api.get(resource_url, params=query).respond(json=data)
    items = list(core.find_by_hash(base_url, src))
    assert items == data


def test_verify_resource_by_id(mocked_api, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = {"sha1": sha1}
    mocked_api.get(registry.full_url(base_url, name)).respond(json=data)
    assert core.verify(base_url, src, id=name)


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
    updated = list(core.update(base_url, name, auth=auth, metadata=metadata))
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
    updated = list(core.update(base_url, name, auth=netrc_auth, metadata=metadata))
    assert updated == [{"metadata": metadata, "name": name}]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def auth_header(auth):
    request = httpx.Request("GET", f"{base_url}/resources/")
    return next(auth.auth_flow(request)).headers.get("Authorization")


@pytest.mark.parametrize("name", [".netrc", "_netrc"])
def test_make_auth_reads_netrc(home, name):
    netrc = home / name
    netrc.write_text("machine localhost\nlogin {}\npassword {}\n".format(*auth))
    netrc.chmod(0o600)
    assert auth_header(core.make_auth(None)) == f"Basic {auth_enc}"


def test_make_auth_prefers_dot_netrc(home):
    (home / ".netrc").write_text(
        "machine localhost\nlogin {}\npassword {}\n".format(*auth)
    )
    (home / ".netrc").chmod(0o600)
    (home / "_netrc").write_text("machine localhost\nlogin other\npassword other\n")
    assert auth_header(core.make_auth(None)) == f"Basic {auth_enc}"


def test_make_auth_without_netrc(home):
    assert core.make_auth(None) is None


def test_check_all_passes(mocked_api, tmp_archive, tmp_path):
    root = tmp_archive.path
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
    mocked_api.get(resource_url, params={"archive": archive_name}).respond(
        json=[{"name": "res_1", "sha1": sha1, "locations": [archive_name]}]
    )
    # check registry's search for empty archives
    mocked_api.get(resource_url, params={"archive": "tape"}).respond(
        json=[{"name": "res_2", "sha1": None, "locations": ["tape"]}]
    )
    mock_archive_locations(mocked_api, archive_name, root, {"res_1": (sha1, "res_1")})
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    try:
        assert script.main(["-r", base_url, "check", "all"]) == 0
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)


@pytest.fixture
def keyless_archive(mocked_api, tmp_archive, tmp_path):
    """An archive with one file, res_1.txt, whose location has no key."""
    root = tmp_archive.path
    src = tmp_path / "res_1.txt"
    src.write_text("contents")
    sha1 = util.hash(src)
    archive.store_resource(tmp_archive, src)
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    mock_archive_locations(mocked_api, archive_name, root, {"res_1": (sha1, None)})
    return root


def test_check_archive_reports_missing_keys(keyless_archive, caplog):
    assert run_main("check", "archive", str(keyless_archive)) == 1
    assert "location has NO key: 1 resources (use -v to list)" in caplog.text
    assert "location key errors: 1" in caplog.text


def test_check_archive_fix_sets_keys(mocked_api, keyless_archive, caplog):
    route = mocked_api.patch(f"{resource_url}res_1/locations/{archive_name}/").respond(
        json={"key": "res_1.txt"}
    )
    assert run_main("check", "archive", "--fix", str(keyless_archive)) == 0
    assert json.loads(route.calls.last.request.content) == {"key": "res_1.txt"}
    assert "Location keys set: 1" in caplog.text
    assert "location key errors: 0" in caplog.text


def test_check_archive_fix_key_refused(mocked_api, keyless_archive, caplog):
    mocked_api.patch(f"{resource_url}res_1/locations/{archive_name}/").respond(
        400, json={"key": ["another resource in this archive already has this key"]}
    )
    assert run_main("check", "archive", "--fix", str(keyless_archive)) == 1
    assert (
        "res_1 : unable to set the key to 'res_1.txt': key: another resource"
    ) in caplog.text
    assert "Location keys set: 0" in caplog.text


def run_main(*argv):
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    try:
        return script.main(["-r", base_url, *argv])
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)


@pytest.mark.posix
def test_init_shared_archive(mocked_api, tmp_path):
    import grp

    group = grp.getgrgid(os.getgid()).gr_name
    root = tmp_path / "archive"
    mocked_api.post(archives_url).respond(201, json={})
    run_main("archive", "init", "--shared", "-g", group, str(root))
    access = archive.ArchiveConfig.load(root).policy.access
    assert access.user is None
    assert access.group == group


@pytest.mark.posix
def test_init_unknown_group(mocked_api, tmp_path, caplog):
    # no registry route is mocked: the archive must not be registered
    root = tmp_path / "archive"
    run_main("archive", "init", "-g", "no-such-group-xyzzy", str(root))
    assert "group 'no-such-group-xyzzy' does not exist" in caplog.text
    assert not root.exists()


def test_exit_status_registry_unreachable(mocked_api, caplog):
    mocked_api.post(bulk_url + "resources/").mock(
        side_effect=httpx.ConnectError("refused")
    )
    assert run_main("info", "res_1") == 1
    assert "unable to contact server" in caplog.text


def test_exit_status_not_authorized(mocked_api, caplog):
    mocked_api.post(datatypes_url).respond(403, json={"detail": "not allowed"})
    assert run_main("dtype", "add", "new-dtype", "text/plain") == 1
    assert "authentication error" in caplog.text


def test_exit_status_no_registry(monkeypatch, caplog):
    monkeypatch.delenv(registry._env_registry, raising=False)
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level
    try:
        assert script.main(["info", "res_1"]) == 1
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)
    assert "supply a registry url" in caplog.text


def test_exit_status_interrupted(monkeypatch):
    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(script, "get_resource_info", interrupt)
    assert run_main("info", "res_1") == 130


full_hash = "0123456789abcdef0123456789abcdef01234567"


def test_search_full_hash_is_exact(mocked_api, capsys):
    mocked_api.get(resource_url, params={"sha1": full_hash}).respond(
        json=[{"name": "res_1"}]
    )
    assert run_main("search", "-H", full_hash) is None
    assert capsys.readouterr().out == "res_1\n"


def test_search_partial_hash(mocked_api, capsys):
    mocked_api.get(resource_url, params={"sha1_contains": "89abcdef"}).respond(
        json=[{"name": "res_1"}]
    )
    run_main("search", "-H", "89abcdef")
    assert capsys.readouterr().out == "res_1\n"


def test_search_by_scheme(mocked_api, capsys):
    mocked_api.get(resource_url, params={"scheme": "tape", "dtype": "raw"}).respond(
        json=[{"name": "res_1"}, {"name": "res_2"}]
    )
    assert run_main("search", "-s", "tape", "-d", "raw") is None
    assert capsys.readouterr().out == "res_1\nres_2\n"


def test_search_by_archive_is_exact(mocked_api, capsys):
    mocked_api.get(resource_url, params={"archive": "starlings"}).respond(
        json=[{"name": "res_1"}]
    )
    assert run_main("search", "-n", "starlings") is None
    assert capsys.readouterr().out == "res_1\n"


def test_search_by_core_fields(mocked_api, capsys):
    mocked_api.get(
        resource_url,
        params={
            "animal__istartswith": "P50",
            "experimenter": "dmeliza",
            "metadata__protocol": "chorus",
        },
    ).respond(json=[{"name": "res_1"}])
    args = ("--animal", "P50", "--experimenter", "dmeliza", "-k", "protocol=chorus")
    assert run_main("search", *args) is None
    assert capsys.readouterr().out == "res_1\n"


def test_modify_dtype(mocked_api, capsys):
    mocked_api.patch(
        registry.full_url(base_url, "res_1"),
        json={"dtype": "intracellular-abfzip", "metadata": {"dtype": "abf"}},
    ).respond(json={"name": "res_1"})
    # a metadata key of the same name stays metadata
    argv = ("--dtype", "intracellular-abfzip", "-k", "dtype=abf", "res_1")
    assert run_main("modify", *argv) is None
    assert json.loads(capsys.readouterr().out) == {"name": "res_1"}


def test_update_dtype_only(mocked_api):
    route = mocked_api.patch(registry.full_url(base_url, "res_1")).respond(
        json={"name": "res_1"}
    )
    list(core.update(base_url, "res_1", dtype="wav"))
    assert json.loads(route.calls.last.request.content) == {
        "dtype": "wav",
        "metadata": {},
    }


def test_modify_core_fields(mocked_api, capsys):
    mocked_api.patch(
        registry.full_url(base_url, "res_1"),
        json={
            "experimenter": "dmeliza",
            "animal": None,
            "metadata": {"protocol": "chorus", "pen": None},
        },
    ).respond(json={"name": "res_1"})
    # an empty value clears a field
    args = ("--experimenter", "dmeliza", "--animal", "", "-k", "protocol=chorus")
    assert run_main("modify", *args, "-K", "pen", "res_1") is None
    assert json.loads(capsys.readouterr().out) == {"name": "res_1"}


@pytest.mark.parametrize(
    "argv",
    [
        ("deposit", "-k", "animal=P50", "archive", "file"),
        ("search", "-k", "experimenter=dmeliza"),
        ("search", "-K", "animal=P50"),
        ("modify", "-k", "experimenter=dmeliza", "res_1"),
        ("modify", "-K", "animal", "res_1"),
    ],
)
def test_core_fields_are_refused_as_metadata(mocked_api, capsys, argv):
    # no registry route is mocked: nothing must be sent
    with pytest.raises(SystemExit) as exc:
        run_main(*argv)
    assert exc.value.code == 2
    assert "is not metadata; use --" in capsys.readouterr().err


def test_verify_name_that_isnt_an_id(mocked_api, tmp_path, capsys):
    # the name can't be an id, so the file is looked up by its hash
    src = tmp_path / "not an id.txt"
    src.write_text("contents")
    mocked_api.get(resource_url, params={"sha1": util.hash(src)}).respond(json=[])
    assert run_main("verify", str(src)) == 1
    assert capsys.readouterr().out == f"{src}: no matches in registry\n"


def test_init_existing_archive(mocked_api, tmp_archive, caplog):
    # no registry route is mocked: the archive must not be registered
    config = tmp_archive.path / "nbank.json"
    before = config.read_text()
    run_main("archive", "init", str(tmp_archive.path))
    assert f"'{config}' already exists" in caplog.text
    assert config.read_text() == before


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True


def test_progress_not_a_terminal():
    stream = io.StringIO()
    progress = script.Progress(stream)
    progress.start("res_1.wav", 100)
    progress("res_1.wav", 50)
    progress.clear()
    assert stream.getvalue() == ""


class FakeClock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(script.time, "monotonic", fake)
    return fake


def shown(stream):
    return [line for line in stream.getvalue().split("\r") if line.strip()]


def test_progress_line(clock):
    stream = FakeTerminal()
    progress = script.Progress(stream)
    progress.start("res_1.wav", 4_000_000_000)
    for clock.now, nbytes in (
        (0.0, 0),  # the first report is shown
        (1.0, 1_000_000_000),
        (1.1, 1_100_000_000),  # too soon after the last, skipped
        (2.0, 2_000_000_000),
    ):
        progress("res_1.wav", nbytes)
    assert shown(stream) == [
        "  res_1.wav  0 B / 4.0 GB (0%)",
        "  res_1.wav  1.0 GB / 4.0 GB (25%)  1.0 GB/s",
        "  res_1.wav  2.0 GB / 4.0 GB (50%)  1.0 GB/s",
    ]


def test_progress_rate_shows_stalls(clock):
    stream = FakeTerminal()
    progress = script.Progress(stream)
    progress.start("res_1.wav", None)
    for clock.now, nbytes in ((0.0, 0), (1.0, 1_000_000_000)):
        progress("res_1.wav", nbytes)
    # nothing arrives for 9 seconds
    clock.now = 10.0
    progress("res_1.wav", 1_010_000_000)
    assert shown(stream)[-1].endswith("  1.1 MB/s")
    # full speed again; once the stall is more than a window ago, it's forgotten
    clock.now = 15.2
    progress("res_1.wav", 1_610_000_000)
    assert shown(stream)[-1].endswith("  115.4 MB/s")


def test_progress_directory_entries(clock):
    stream = FakeTerminal()
    progress = script.Progress(stream)
    progress.start("res_2", None)
    progress("sub/a", 500)
    clock.now = 2.0
    progress("sub/a", 2_000_500)
    assert shown(stream)[-1] == "  res_2/sub/a  2.0 MB  1.0 MB/s"


@pytest.mark.parametrize("terminal", [True, False])
def test_progress_summary(clock, terminal):
    progress = script.Progress(FakeTerminal() if terminal else io.StringIO())
    progress.start("res_2", None)
    progress("sub/a", 1_000_000_000)
    progress("sub/b", 500_000_000)
    progress("sub/b", 1_000_000_000)
    clock.now = 2.0
    assert progress.summary() == "2.0 GB, 1.0 GB/s"


def test_progress_clears_line_before_log_messages():
    logged = io.StringIO()
    handler = logging.StreamHandler(logged)
    log = logging.getLogger("nbank")
    log.addHandler(handler)
    stream = FakeTerminal()
    try:
        with script.Progress(stream) as progress:
            progress.start("res_1.wav", None)
            progress("res_1.wav", 1000)
            log.error("a message")
            assert stream.getvalue().endswith("\r")
            assert progress._width == 0
        assert handler.filters == []
    finally:
        log.removeHandler(handler)
    assert logged.getvalue() == "a message\n"


def test_check_tar_missing_file(tmp_path, caplog):
    assert run_main("check", "tar", str(tmp_path / "missing.tar")) == 1
    assert "unable to read" in caplog.text


@pytest.fixture
def exportable(respx_mock, tmp_path):
    """Two resources in a local archive, registered with their hashes."""
    skip_unless_posix()
    from test.test_transfer import FakeBulkLocations, location, record

    cfg = archive.create(tmp_path / "archive", base_url)
    records = []
    for name in ("res_1", "res_2"):
        src = tmp_path / f"{name}.txt"
        src.write_text(f"contents of {name}")
        sha1 = util.hash(src)
        archive.store_resource(cfg, src, id=name)
        records.append(
            record(name, sha1, location("arch", cfg.path, name, key=f"{name}.txt"))
        )
    FakeBulkLocations(respx_mock, records)
    full = [
        {**r, "dtype": "txt", "metadata": {"n": i}, "locations": ["arch"]}
        for i, r in enumerate(records)
    ]
    respx_mock.post(bulk_url + "resources/").respond(
        200, content="".join(json.dumps(r) + "\n" for r in full).encode()
    )
    return cfg


def tar_names(path):
    import tarfile

    with tarfile.open(path) as tar:
        return tar.getnames()


@respx.mock(assert_all_mocked=True)
def test_export(exportable, tmp_path, caplog):
    out = tmp_path / "export.tar"
    assert run_main("export", str(out), "res_1", "res_2") is None
    assert tar_names(out) == ["res_1.txt", "res_2.txt"]
    assert "Resources requested: 2; exported: 2 (" in caplog.text
    assert "failed: 0" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_ids_from_file(exportable, tmp_path):
    ids = tmp_path / "ids"
    ids.write_text("res_2\n\nres_1\n")
    out = tmp_path / "export.tar"
    assert run_main("export", "-f", str(ids), str(out)) is None
    assert tar_names(out) == ["res_2.txt", "res_1.txt"]


@respx.mock(assert_all_mocked=True)
def test_export_reports_missing(exportable, tmp_path, caplog):
    out = tmp_path / "export.tar"
    assert run_main("export", str(out), "res_1", "missing") == 1
    assert tar_names(out) == ["res_1.txt"]
    assert "missing -> not in the registry" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_leaves_out_changed_file(exportable, tmp_path, caplog):
    path = archive.resource_path(exportable, "res_1", resolve_ext=True)
    path.chmod(0o644)
    path.write_text("changed")
    out = tmp_path / "export.tar"
    assert run_main("export", str(out), "res_1", "res_2") == 1
    assert tar_names(out) == ["res_2.txt"]
    assert "res_1 -> contents don't match" in caplog.text
    assert "exported: 1 (" in caplog.text
    assert "failed: 1 (marked with ✗ above)" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_never_overwrites(exportable, tmp_path, caplog):
    out = tmp_path / "export.tar"
    out.write_text("already here")
    assert run_main("export", str(out), "res_1") == 1
    assert out.read_text() == "already here"
    assert "unable to export to" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_nothing_found(exportable, tmp_path, caplog):
    out = tmp_path / "export.tar"
    assert run_main("export", str(out), "missing", "missing", "gone") == 1
    assert "Resources requested: 2; exported: 0 (0 B); failed: 2" in caplog.text
    assert not out.exists()


def test_export_no_ids(tmp_path, caplog):
    assert run_main("export", str(tmp_path / "export.tar")) == 1
    assert "no identifiers" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_zip(exportable, tmp_path):
    import zipfile

    out = tmp_path / "export.zip"
    assert run_main("export", "--compress", str(out), "res_1", "res_2") is None
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["res_1.txt", "res_2.txt"]
        assert zf.read("res_2.txt") == b"contents of res_2"
        assert zf.getinfo("res_1.txt").compress_type == zipfile.ZIP_DEFLATED


@respx.mock(assert_all_mocked=True)
def test_export_directory(exportable, tmp_path):
    out = tmp_path / "exported" / "here"
    assert run_main("export", str(out), "res_1", "res_2") is None
    assert sorted(p.name for p in out.iterdir()) == ["res_1.txt", "res_2.txt"]
    assert (out / "res_1.txt").read_text() == "contents of res_1"


@respx.mock(assert_all_mocked=True)
def test_export_directory_skips_existing(exportable, tmp_path, caplog):
    out = tmp_path / "exported"
    out.mkdir()
    (out / "res_1.txt").write_text("already here")
    assert run_main("export", str(out), "res_1", "res_2") == 1
    assert (out / "res_1.txt").read_text() == "already here"
    assert (out / "res_2.txt").read_text() == "contents of res_2"
    assert "already exists" in caplog.text


def test_export_compress_only_for_zip(tmp_path, caplog):
    assert run_main("export", "--compress", str(tmp_path / "x.tar"), "res_1") == 1
    assert "only applies to zip" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_refuses_compressed_tar(exportable, tmp_path, caplog):
    out = tmp_path / "export.tar.gz"
    assert run_main("export", str(out), "res_1") == 1
    assert not out.exists()
    assert "use .tar or .zip" in caplog.text


@respx.mock(assert_all_mocked=True)
def test_export_manifest(exportable, tmp_path):
    import zipfile

    path = archive.resource_path(exportable, "res_2", resolve_ext=True)
    path.chmod(0o644)
    path.write_text("changed")
    out = tmp_path / "export.zip"
    assert run_main("export", "--manifest", str(out), "res_1", "res_2") == 1
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["res_1.txt", "manifest.json"]
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["registry"] == base_url
    [entry] = manifest["resources"]
    assert entry["name"] == "res_1"
    assert entry["path"] == "res_1.txt"
    assert entry["metadata"] == {"n": 0}
    assert entry["dtype"] == "txt"
    assert "locations" not in entry


@pytest.mark.posix
@respx.mock(assert_all_mocked=True)
def test_export_manifest_name_taken(respx_mock, tmp_path, caplog):
    from test.test_transfer import FakeBulkLocations, location, record

    cfg = archive.create(tmp_path / "archive", base_url)
    src = tmp_path / "manifest.json"
    src.write_text("a resource called manifest")
    archive.store_resource(cfg, src, id="manifest")
    FakeBulkLocations(
        respx_mock,
        [
            record(
                "manifest",
                None,
                location("arch", cfg.path, "manifest", key="manifest.json"),
            )
        ],
    )
    out = tmp_path / "export.tar"
    assert run_main("export", "--manifest", str(out), "manifest") == 1
    assert "there can't be a manifest" in caplog.text
    assert not out.exists()


@respx.mock(assert_all_mocked=True)
def test_export_manifest_already_in_directory(exportable, tmp_path, caplog):
    out = tmp_path / "exported"
    out.mkdir()
    (out / "manifest.json").write_text("already here")
    assert run_main("export", "--manifest", str(out), "res_1") == 1
    assert "already exists" in caplog.text
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json"]


@pytest.mark.posix
@respx.mock(assert_all_mocked=True, assert_all_called=False)
def test_register_tape_skips_manifest(respx_mock, tmp_path, caplog):
    import tarfile

    looked_up = respx_mock.get(resource_url + "manifest/").respond(
        200, json={"name": "manifest", "locations": []}
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    tar = tmp_path / "export.tar"
    with tarfile.open(tar, "w") as t:
        t.add(manifest, arcname="manifest.json")
    run_main("archive", "register", "tape", "-y", "tape01", "1", str(tar))
    assert not looked_up.called
    assert "manifest.json -> manifest, skipping" in caplog.text


@pytest.mark.parametrize("with_archive", [False, True])
def test_check_tar_read_error(mocked_api, failing_tar, caplog, with_archive):
    members = [("res_1", b"one"), ("res_2", b"x" * 50_000), ("res_3", b"three")]
    for name in ("res_1", "res_2"):
        mocked_api.get(resource_url + f"{name}/").respond(
            json={"name": name, "sha1": None, "locations": ["tape"]}
        )
    argv = ["check", "tar"]
    if with_archive:
        mocked_api.get(archives_url + "tape/").respond(json={"name": "tape"})
        mock_archive_locations(
            mocked_api,
            "tape",
            "tape:1",
            {name: (None, name) for name in ("res_1", "res_2", "res_3")},
            scheme="tape",
        )
        argv += ["--archive", "tape"]
    # fails partway through res_2
    path = failing_tar(members, 3 * 10240)
    assert run_main(*argv, str(path)) == 1
    assert "res_1 -> OK" in caplog.text
    assert "✗ res_2 -> unable to read; stopping" in caplog.text
    assert "Input/output error after reading 30720 bytes in 3 blocks" in caplog.text
    assert "Resources checked: 2; failed: 1" in caplog.text
    assert "MISSING" not in caplog.text
    if with_archive:
        assert "res_3: not checked, as reading stopped before it" in caplog.text
        assert "Missing from the tar file: 0; not checked (reading stopped): 1" in (
            caplog.text
        )


def test_fetch_from_tar_read_error(mocked_api, failing_tar, tmp_path, capsys, caplog):
    members = [("res_1", b"one"), ("res_2", b"x" * 50_000), ("res_3", b"three")]
    mocked_api.post(bulk_url + "resources/").respond(
        stream=(
            json.dumps({"name": name, "sha1": None, "filename": name}).encode() + b"\n"
            for name, _ in members
        )
    )
    # fails partway through res_2
    path = failing_tar(members, 3 * 10240)
    dest = tmp_path / "out"
    dest.mkdir()
    ids = [name for name, _ in members]
    assert run_main("fetch", "--tar", str(path), "-d", str(dest), *ids) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"res_1               \t-> {dest / 'res_1'} (no registered hash to check)",
        "res_2               \t-> (unable to read; stopping)",
        "res_3               \t-> (not reached, as reading stopped)",
    ]
    assert "unable to read" in caplog.text
    assert [p.name for p in dest.iterdir()] == ["res_1"]


def test_check_tar_truncated(mocked_api, tmp_path, caplog):
    from test.test_transfer import tar_bytes

    members = [("res_1", b"one"), ("res_2", b"x" * 50_000)]
    for name in ("res_1", "res_2"):
        mocked_api.get(resource_url + f"{name}/").respond(
            json={"name": name, "sha1": None, "locations": ["tape"]}
        )
    mocked_api.get(archives_url + "tape/").respond(json={"name": "tape"})
    mock_archive_locations(
        mocked_api,
        "tape",
        "tape:1",
        {name: (None, name) for name in ("res_1", "res_2")},
        scheme="tape",
    )
    # the tape ran out partway through res_2
    path = tmp_path / "truncated.tar"
    path.write_bytes(tar_bytes(members)[: 512 + 512 + 512 + 20_000])
    assert run_main("check", "tar", "--archive", "tape", str(path)) == 1
    assert "res_1 -> OK" in caplog.text
    assert "✗ res_2 -> unable to read; stopping" in caplog.text
    assert "ends partway through 'res_2'" in caplog.text
    assert "Resources checked: 2; failed: 1" in caplog.text


@pytest.mark.parametrize(
    "user, lines",
    [
        (None, ["user: anonymous (no credentials; use -a or a .netrc file)"]),
        (
            {
                "username": "dmeliza",
                "superuser": False,
                "permissions": ["add_resource"],
            },
            ["user: dmeliza", "permissions: add_resource"],
        ),
        (
            {"username": "reader", "superuser": False, "permissions": []},
            ["user: reader", "permissions: none"],
        ),
        (
            {"username": "admin", "superuser": True, "permissions": ["add_resource"]},
            ["user: admin (superuser)"],
        ),
    ],
)
def test_registry_info_shows_the_user(mocked_api, caplog, user, lines):
    caplog.set_level(logging.INFO, logger="nbank")
    mocked_api.get(f"{base_url}info/").respond(
        json={"name": "django-neurobank", "api_version": "2.0", "user": user}
    )
    assert run_main("registry", "info") is None
    messages = [r.getMessage() for r in caplog.records]
    assert "  - name: django-neurobank" in messages
    assert [m for m in messages if m.startswith(("  - user", "  - perm"))] == [
        f"  - {line}" for line in lines
    ]


def test_cli_reports_a_registry_version_it_cant_use(mocked_api, monkeypatch, caplog):
    monkeypatch.setattr(util, "_checked_registries", set())
    mocked_api.get(f"{base_url}info/").respond(json={"api_version": "1.3"})
    assert run_main("search", "-n", "res_1") == 1
    assert "has API version 1.3" in caplog.text


def test_registry_info_shows_a_registry_it_cant_use(mocked_api, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="nbank")
    monkeypatch.setattr(util, "_checked_registries", set())
    mocked_api.get(f"{base_url}info/").respond(json={"api_version": "1.3"})
    assert run_main("registry", "info") is None
    assert "api_version: 1.3" in caplog.text


def test_registry_info_not_a_registry(mocked_api, caplog):
    mocked_api.get(f"{base_url}info/").respond(404)
    assert run_main("registry", "info") == 1
    assert f"{base_url} is not a neurobank registry (no info/)" in caplog.text


def test_registry_info_without_user(mocked_api, caplog):
    caplog.set_level(logging.INFO, logger="nbank")
    mocked_api.get(f"{base_url}info/").respond(json={"api_version": "1.2"})
    assert run_main("registry", "info") is None
    assert "user" not in caplog.text


@pytest.mark.parametrize(
    "superseded, sent", [(False, "false"), (True, "true"), (None, None)]
)
def test_search_superseded(mocked_api, superseded, sent):
    route = mocked_api.get(resource_url).respond(json=[])
    list(core.search(base_url, dtype="wav", superseded=superseded))
    assert route.calls.last.request.url.params.get("superseded") == sent


def test_search_leaves_out_superseded_by_default(mocked_api):
    route = mocked_api.get(resource_url).respond(json=[])
    list(core.search(base_url, dtype="wav"))
    assert route.calls.last.request.url.params["superseded"] == "false"


def test_find_by_hash_finds_superseded_resources(mocked_api, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("contents")
    route = mocked_api.get(resource_url).respond(json=[{"name": "res_1"}])
    assert [r["name"] for r in core.find_by_hash(base_url, path)] == ["res_1"]
    assert "superseded" not in route.calls.last.request.url.params


@pytest.mark.parametrize(
    "argv, sent",
    [
        (["-d", "wav"], "false"),
        (["-d", "wav", "--only-superseded"], "true"),
        (["--only-superseded"], "true"),
        (["-d", "wav", "--with-superseded"], None),
    ],
)
def test_search_superseded_options(mocked_api, capsys, argv, sent):
    route = mocked_api.get(resource_url).respond(json=[{"name": "res_1"}])
    assert run_main("search", *argv) is None
    assert route.calls.last.request.url.params.get("superseded") == sent
    assert capsys.readouterr().out == "res_1\n"


@pytest.mark.parametrize("argv", [[], ["--with-superseded"]])
def test_search_needs_a_filter(mocked_api, caplog, argv):
    assert run_main("search", *argv) == 1
    assert "at least one filter" in caplog.text


def superseded_record(name, by=None, note=""):
    return {
        "name": name,
        "superseded_on": "2026-10-22T12:00:00Z",
        "superseded_by": by,
        "superseded_note": note,
    }


def current_record(name):
    return {"name": name, "superseded_on": None, "superseded_by": None}


def test_check_superseded(mocked_api, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="nbank")
    route = mocked_api.post(f"{bulk_url}resources/").respond(
        text="\n".join(
            json.dumps(r)
            for r in (
                current_record("res_1"),
                superseded_record("res_2", by="res_4", note="wrong animal"),
                superseded_record("res_3"),
            )
        )
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("res_3\nres_5\n\nres_1\n"))
    assert run_main("check", "superseded", "-@", "res_1", "res_2") == 1
    assert json.loads(route.calls.last.request.content)["names"] == [
        "res_1",
        "res_2",
        "res_3",
        "res_5",
    ]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        " - res_2: superseded on 2026-10-22T12:00:00Z by res_4 (wrong animal)",
        " - res_3: superseded on 2026-10-22T12:00:00Z",
        " - res_5: not in the registry",
    ]
    assert "4 resources checked; superseded: 2; not in the registry: 1" in caplog.text


def test_check_superseded_all_current(mocked_api, monkeypatch):
    monkeypatch.setattr(util, "bulk_batch_size", 1)
    route = mocked_api.post(f"{bulk_url}resources/")
    route.side_effect = lambda request: httpx.Response(
        200,
        text=json.dumps(current_record(json.loads(request.content)["names"][0])),
    )
    assert run_main("check", "superseded", "res_1", "res_2") == 0
    assert route.call_count == 2


def test_check_superseded_needs_ids(mocked_api, caplog):
    assert run_main("check", "superseded") == 1
    assert "no identifiers given" in caplog.text


def test_verify_refuses_a_directory(mocked_api, tmp_path, capsys):
    assert run_main("verify", str(tmp_path)) == 1
    assert (
        capsys.readouterr().out
        == f"{tmp_path}: is a directory, which isn't a resource\n"
    )


def test_rename_to_filename_never_replaces(tmp_path):
    path = tmp_path / "res_1.TXT"
    path.write_text("one")
    (tmp_path / "res_1.txt").write_text("other")
    finding = check.Finding(check.Status.OK, "res_1", path)
    with pytest.raises(FileExistsError):
        script._rename_to_filename(finding, "res_1.txt")
    assert path.read_text() == "one"
    assert (tmp_path / "res_1.txt").read_text() == "other"
