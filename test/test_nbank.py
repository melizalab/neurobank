# -*- coding: utf-8 -*-
# -*- mode: python -*-
from test.test_registry import archives_url, base_url, bulk_url, resource_url

import httpx
import pytest
import respx

from nbank import archive, core, registry, util

archive_name = "archive"


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
    mocked_api.post(
        resource_url,
        json={
            "name": name,
            "dtype": dtype,
            "locations": [archive_name],
            "sha1": sha1,
            "metadata": metadata,
        },
    ).respond(json={"name": name})
    items = list(core.deposit(root, files=[src], dtype=dtype, hash=True, **metadata))
    assert items == [{"source": src, "id": name}]


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
    with pytest.raises(OSError):
        _ = list(core.deposit(root, files=[src], dtype=dtype))

    # tgt is not writable
    src.chmod(0o400)
    tgt_dir = archive.resource_path(tmp_archive, name).parent
    tgt_dir.mkdir(0o444, parents=True)
    with pytest.raises(OSError):
        _ = list(core.deposit(root, files=[src], dtype=dtype))


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
    mocked_api.post(
        registry.url_join(bulk_url, "resources/"), json={"names": names}
    ).respond(json=data)
    info = core.describe_many(base_url, *names)
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
    from nbank.archive import resource_path

    name = "dummy_3"
    mocked_api.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/")
    ).respond(
        json=[
            {
                "scheme": "neurobank",
                "root": "/home/data/starlings",
                "resource_name": name,
            }
        ],
    )
    items = list(core.find(base_url, name))
    assert items == [resource_path("/home/data/starlings", name)]
    item = core.get(base_url, name)
    assert item == resource_path("/home/data/starlings", name)


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
        registry.full_url(base_url, name), json={"metadata": metadata}
    ).respond(
        json={"name": name, "metadata": metadata},
    )
    updated = list(core.update(base_url, name, **metadata))
    assert updated == [{"metadata": metadata, "name": name}]
