# -*- coding: utf-8 -*-
# -*- mode: python -*-
import pytest

import requests
import responses
from responses import matchers

from nbank import core, registry, archive, util

from test.test_registry import (
    base_url,
    resource_url,
    archives_url,
)

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
def mocked_resps():
    with responses.RequestsMock() as rsps:
        yield rsps


def test_deposit_resource(mocked_resps, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    metadata = {"experimenter": "dmeliza"}
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    mocked_resps.get(
        archives_url,
        json=[{"name": archive_name, "root": str(root)}],
        match=[
            matchers.query_param_matcher({"scheme": "neurobank", "root": str(root)})
        ],
    )
    mocked_resps.post(
        resource_url,
        json={"name": name},
        match=[
            matchers.json_params_matcher(
                {
                    "name": name,
                    "dtype": dtype,
                    "locations": [archive_name],
                    "sha1": sha1,
                    "metadata": metadata,
                }
            )
        ],
    )
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


def test_deposit_resource_archive_errors(mocked_resps, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    dtype = "dummy-dtype"
    src = tmp_path / "dummy"
    mocked_resps.get(
        archives_url,
        json=[],
        match=[
            matchers.query_param_matcher({"scheme": "neurobank", "root": str(root)})
        ],
    )
    # invalid archive
    with pytest.raises(ValueError):
        _ = list(core.deposit(tmp_path, files=[src], dtype=dtype))

    # archive not in registry
    with pytest.raises(RuntimeError):
        _ = list(core.deposit(root, files=[src], dtype=dtype))


def test_deposit_resource_source_errors(mocked_resps, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    src = tmp_path / name
    mocked_resps.get(
        archives_url,
        json=[{"name": archive_name, "root": str(root)}],
        match=[
            matchers.query_param_matcher({"scheme": "neurobank", "root": str(root)})
        ],
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


def test_deposit_resource_registry_duplicate(mocked_resps, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    mocked_resps.get(
        archives_url,
        json=[{"name": archive_name, "root": str(root)}],
        match=[
            matchers.query_param_matcher({"scheme": "neurobank", "root": str(root)})
        ],
    )
    # registry will respond with 400 if the resource cannot be created for some
    # reason (duplicate/invalid name, duplicate/invalid sha1, invalid dtype)
    mocked_resps.post(
        resource_url,
        json={"error": "something was not valid"},
        status=400,
        match=[
            matchers.json_params_matcher(
                {
                    "name": name,
                    "dtype": dtype,
                    "locations": [archive_name],
                    "metadata": {},
                }
            )
        ],
    )
    with pytest.raises(requests.exceptions.HTTPError):
        _ = list(core.deposit(root, files=[src], dtype=dtype, hash=False))


def test_describe_resource(mocked_resps):
    name = "dummy_2"
    data = {"you": "found me"}
    mocked_resps.get(registry.full_url(base_url, name), json=data)
    info = core.describe(base_url, name)
    assert info == data


def test_describe_nonexistent_resource(mocked_resps):
    name = "dummy_2"
    data = {"detail": "not found"}
    mocked_resps.get(registry.full_url(base_url, name), json=data, status=404)
    info = core.describe(base_url, name)
    assert info is None


def test_search_resource(mocked_resps):
    data = [{"super": "great!"}, {"also": "awesome!"}]
    query = {"sha1": "abc23"}
    mocked_resps.get(
        resource_url, json=data, match=[matchers.query_param_matcher(query)]
    )
    items = list(core.search(base_url, **query))
    assert items == data


def test_search_nonexistent_resource(mocked_resps):
    data = []
    query = {"sha1": "abc23a"}
    mocked_resps.get(
        resource_url, json=data, match=[matchers.query_param_matcher(query)]
    )
    items = list(core.search(base_url, **query))
    assert items == data


def test_find_resource_location(mocked_resps):
    from nbank.archive import resource_path

    name = "dummy_3"
    mocked_resps.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/"),
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


def test_find_resource_location_nonexistent(mocked_resps):
    name = "dummy_4"
    mocked_resps.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/"), json=[]
    )
    item = core.get(base_url, name)
    assert item is None


def test_verify_resource_by_hash(mocked_resps, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = [{"sha1": sha1}]
    query = {"sha1": sha1}
    mocked_resps.get(
        resource_url, json=data, match=[matchers.query_param_matcher(query)]
    )
    items = list(core.verify(base_url, src))
    assert items == data


def test_verify_resource_by_id(mocked_resps, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = {"sha1": sha1}
    mocked_resps.get(registry.full_url(base_url, name), json=data)
    assert core.verify(base_url, src, name)


def test_update_metadata(mocked_resps):
    name = "dummy_11"
    metadata = {"new": "value"}
    mocked_resps.patch(
        registry.full_url(base_url, name),
        json={"name": name, "metadata": metadata},
        match=[matchers.json_params_matcher({"metadata": metadata})],
    )
    updated = list(core.update(base_url, name, **metadata))
    assert updated == [{"metadata": metadata, "name": name}]
