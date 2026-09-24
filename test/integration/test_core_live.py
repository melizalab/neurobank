# -*- mode: python -*-
"""Tests of the nbank.core API against a live registry.

NB: fixtures are defined in the conftest module
"""

import hashlib
import re
import uuid

import httpx
import pytest

from nbank import archive as nbank_archive
from nbank import core, util
from nbank import registry as reg


def write_file(path, contents):
    path.write_text(contents)
    return path


def deposit_one(registry, archive, dtype, src, **kwargs):
    """Deposits src and returns the id of the new resource."""
    [item] = core.deposit(
        archive.path, [src], dtype=dtype, auth=registry.auth, **kwargs
    )
    return item["id"]


def test_deposit(registry, archive, dtype, tmp_path, unique):
    name = unique("res")
    src = write_file(tmp_path / f"{name}.txt", name)
    sha1 = util.hash(src)
    items = list(
        core.deposit(
            archive.path,
            [src],
            dtype=dtype,
            hash=True,
            auth=registry.auth,
            experimenter="dmeliza",
        )
    )
    assert items == [{"source": src, "id": name}]
    assert not src.exists()
    stored = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    assert stored.read_text() == name

    record = core.describe(registry.url, name)
    assert record["sha1"] == sha1
    assert record["dtype"] == dtype
    assert record["metadata"] == {"experimenter": "dmeliza"}
    assert record["locations"] == [archive.name]
    assert record["created_by"] == registry.auth[0]


def test_deposit_without_hash(registry, archive, dtype, tmp_path, unique):
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    assert core.describe(registry.url, name)["sha1"] is None


def test_deposit_several(registry, archive, dtype, tmp_path, unique):
    names = [unique("res") for _ in range(3)]
    files = [write_file(tmp_path / f"{n}.txt", n) for n in names]
    items = list(core.deposit(archive.path, files, dtype=dtype, auth=registry.auth))
    assert [item["id"] for item in items] == names
    found = {r["name"] for r in core.describe_many(registry.url, *names)}
    assert found == set(names)


def test_deposit_directory(registry, make_archive, dtype, tmp_path, unique):
    archive = make_archive(require_hash=False, allow_directories=True)
    name = unique("res")
    src = tmp_path / name
    src.mkdir()
    write_file(src / "a.txt", "first")
    write_file(src / "b.txt", "second")
    sha1 = util.hash(src)
    deposit_one(registry, archive, dtype, src, hash=True)
    assert core.describe(registry.url, name)["sha1"] == sha1
    assert nbank_archive.resource_path(archive.config, name).is_dir()


def test_deposit_auto_id(registry, make_archive, dtype, tmp_path, unique):
    archive = make_archive(require_hash=False, auto_identifiers=True)
    stem = unique("res")
    src = write_file(tmp_path / f"{stem}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    assert name != stem
    assert re.fullmatch(r"[-_0-9a-zA-Z]+", name)
    assert core.describe(registry.url, name) is not None
    assert nbank_archive.resource_path(archive.config, name, resolve_ext=True).exists()


def test_deposit_auto_id_uuid(registry, make_archive, dtype, tmp_path, unique):
    archive = make_archive(
        require_hash=False, auto_identifiers=True, auto_id_type="uuid"
    )
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    assert str(uuid.UUID(name)) == name
    assert core.describe(registry.url, name) is not None


def test_deposit_duplicate_name(registry, archive, dtype, tmp_path, unique):
    name = unique("res")
    first = write_file(tmp_path / f"{name}.txt", "first")
    deposit_one(registry, archive, dtype, first)
    second = write_file(tmp_path / f"{name}.dat", "second")
    with pytest.raises(httpx.HTTPStatusError) as err:
        deposit_one(registry, archive, dtype, second)
    assert err.value.response.status_code == 400
    assert "name" in err.value.response.json()
    assert second.exists()


def test_deposit_duplicate_hash(registry, archive, dtype, tmp_path, unique):
    first = write_file(tmp_path / f"{unique('res')}.txt", "same contents")
    deposit_one(registry, archive, dtype, first, hash=True)
    second = write_file(tmp_path / f"{unique('res')}.txt", "same contents")
    with pytest.raises(httpx.HTTPStatusError) as err:
        deposit_one(registry, archive, dtype, second, hash=True)
    assert err.value.response.status_code == 400
    assert "sha1" in err.value.response.json()
    assert second.exists()


def test_deposit_unknown_dtype(registry, archive, tmp_path, unique):
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    with pytest.raises(httpx.HTTPStatusError) as err:
        deposit_one(registry, archive, "no-such-dtype", src)
    assert err.value.response.status_code == 400
    assert "dtype" in err.value.response.json()
    assert src.exists()


def test_deposit_unregistered_archive(registry, dtype, tmp_path):
    config = nbank_archive.create(tmp_path / "unregistered", registry.url)
    with pytest.raises(RuntimeError):
        list(core.deposit(config["path"], [], dtype=dtype, auth=registry.auth))


def test_describe_missing(registry, unique):
    assert core.describe(registry.url, unique("missing")) is None


def test_describe_many_skips_missing(registry, register, unique):
    a, b = register()["name"], register()["name"]
    records = list(core.describe_many(registry.url, a, unique("missing"), b))
    assert {r["name"] for r in records} == {a, b}


def test_search_by_sha1(registry, register, unique):
    name = unique("res")
    sha1 = hashlib.sha1(name.encode()).hexdigest()
    register(name, sha1=sha1)
    assert [r["name"] for r in core.search(registry.url, sha1=sha1)] == [name]
    other = hashlib.sha1(unique("other").encode()).hexdigest()
    assert list(core.search(registry.url, sha1=other)) == []


def test_search_by_name_and_dtype(registry, register, dtype, unique):
    tag = unique("tag")
    names = {register(f"{tag}-{i}")["name"] for i in range(3)}
    register()
    assert {r["name"] for r in core.search(registry.url, name=tag)} == names
    assert len(list(core.search(registry.url, dtype=dtype))) == 4


def test_search_by_archive(registry, register, archive, dtype):
    stored = {register(archive=archive.name)["name"] for _ in range(2)}
    register()
    found = core.search(registry.url, dtype=dtype, location=archive.name)
    assert {r["name"] for r in found} == stored


def test_search_by_metadata(registry, register, dtype, unique):
    tag = unique("grp")
    a = register(group=tag, n=3)["name"]
    b = register(group=tag, n=4)["name"]
    c = register(group="other")["name"]

    def search(**params):
        return {r["name"] for r in core.search(registry.url, dtype=dtype, **params)}

    assert search(metadata__group=tag) == {a, b}
    assert search(metadata__n="3") == {a}
    assert search(metadata__group__neq=tag) == {c}


def test_search_is_paginated(registry, client, register, dtype):
    names = [register()["name"] for _ in range(12)]
    url, _ = reg.find_resource(registry.url)
    r = client.get(url, params={"dtype": dtype})
    assert "next" in r.links
    found = [r["name"] for r in core.search(registry.url, dtype=dtype)]
    assert len(found) == len(set(found))
    assert set(found) == set(names)


def test_get_deposited(registry, archive, dtype, tmp_path, unique):
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    resource = core.get(registry.url, name)
    assert resource.path == nbank_archive.resource_path(
        archive.config, name, resolve_ext=True
    )


def test_find_all_locations(
    registry, client, archive, make_archive, dtype, tmp_path, unique
):
    other = make_archive(require_hash=False)
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    url, body = reg.add_location(registry.url, name, other.name)
    client.post(url, json=body).raise_for_status()
    copy = write_file(tmp_path / "copy.txt", "contents")
    nbank_archive.store_resource(other.config, copy, id=name)

    paths = {r.path for r in core.find(registry.url, name)}
    assert paths == {
        nbank_archive.resource_path(archive.config, name, resolve_ext=True),
        nbank_archive.resource_path(other.config, name, resolve_ext=True),
    }


@pytest.mark.xfail(
    strict=True, reason="find yields None for locations whose file is missing"
)
def test_find_skips_missing_files(
    registry, client, archive, make_archive, dtype, tmp_path, unique
):
    other = make_archive(require_hash=False)
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    url, body = reg.add_location(registry.url, name, other.name)
    client.post(url, json=body).raise_for_status()

    resources = list(core.find(registry.url, name))
    assert len(resources) == 1
    assert resources[0] is not None


@pytest.mark.xfail(strict=True, reason="get raises on a 404 instead of returning None")
def test_get_missing(registry, unique):
    assert core.get(registry.url, unique("missing")) is None


def test_verify_by_hash(registry, register, tmp_path, unique):
    src = write_file(tmp_path / "file.txt", unique("contents"))
    name = register(sha1=util.hash(src))["name"]
    assert [r["name"] for r in core.verify(registry.url, src)] == [name]
    other = write_file(tmp_path / "other.txt", unique("contents"))
    assert list(core.verify(registry.url, other)) == []


def test_verify_by_id(registry, register, tmp_path, unique):
    src = write_file(tmp_path / "file.txt", unique("contents"))
    name = register(sha1=util.hash(src))["name"]
    assert core.verify(registry.url, src, name) is True
    other = write_file(tmp_path / "other.txt", unique("contents"))
    assert core.verify(registry.url, other, name) is False


def test_verify_missing_id(registry, tmp_path, unique):
    src = write_file(tmp_path / "file.txt", "contents")
    with pytest.raises(ValueError):
        core.verify(registry.url, src, unique("missing"))


def test_update_metadata(registry, register):
    name = register(a=1, b=2)["name"]
    [result] = core.update(registry.url, name, auth=registry.auth, b=3, c="new")
    assert result["metadata"] == {"a": 1, "b": 3, "c": "new"}
    [result] = core.update(registry.url, name, auth=registry.auth, a=None)
    assert result["metadata"] == {"b": 3, "c": "new"}
    assert core.describe(registry.url, name)["metadata"] == result["metadata"]


def test_update_several(registry, register):
    names = [register()["name"] for _ in range(2)]
    results = list(core.update(registry.url, *names, auth=registry.auth, k="v"))
    assert [r["name"] for r in results] == names
    assert all(r["metadata"] == {"k": "v"} for r in results)


@pytest.mark.xfail(strict=True, reason="update raises after reporting a 404")
def test_update_missing(registry, register, unique):
    missing = unique("missing")
    name = register()["name"]
    results = list(core.update(registry.url, missing, name, auth=registry.auth, k="v"))
    assert results[0] == {"name": missing, "error": "not found"}
    assert results[1]["name"] == name


@pytest.mark.skip(reason="downloads require a web server (e.g., nginx) with sendfile")
def test_fetch():
    pass
