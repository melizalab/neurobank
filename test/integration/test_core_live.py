# -*- mode: python -*-
"""Tests of the nbank.core API against a live registry.

NB: fixtures are defined in the conftest module
"""

import hashlib
import json
import re
import uuid

import httpx
import pytest

from nbank import archive as nbank_archive
from nbank import core, readers, util
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


def test_deposit(registry, archive, dtype, tmp_path, unique, location_key):
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
            metadata={"pen": 1},
        )
    )
    assert items == [{"source": src, "id": name}]
    assert not src.exists()
    stored = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    assert stored.read_text() == name

    record = core.describe(registry.url, name)
    assert record["sha1"] == sha1
    assert record["dtype"] == dtype
    assert record["metadata"] == {"pen": 1}
    assert record["locations"] == [archive.name]
    assert record["created_by"] == registry.auth[0]
    assert location_key(name, archive.name) == stored.name == f"{name}.txt"


def test_deposit_core_fields(core_fields, registry, archive, dtype, tmp_path, unique):
    animal, _ = core_fields
    name = unique("res")
    src = write_file(tmp_path / f"{name}.txt", name)
    user = registry.auth[0]
    items = core.deposit(
        archive.path,
        [src],
        dtype=dtype,
        auth=registry.auth,
        metadata={"experimenter": user, "animal": animal, "protocol": "chorus"},
    )
    assert list(items) == [{"source": src, "id": name}]
    record = core.describe(registry.url, name)
    assert (record["experimenter"], record["animal"]) == (user, animal)
    assert record["metadata"]["protocol"] == "chorus"


def test_deposit_reads_metadata_from_the_file(
    core_fields, registry, archive, dtype, tmp_path, unique, monkeypatch
):
    from test.test_readers import FakeEntryPoint, fake_reader

    animal, _ = core_fields
    user = registry.auth[0]
    monkeypatch.setattr(
        readers, "entry_points", lambda group: [FakeEntryPoint(dtype, fake_reader)]
    )
    name = unique("res")
    src = tmp_path / name
    src.write_text(
        json.dumps(
            {
                "$schema": "fake",
                "metadata": {"experimenter": user, "animal": animal, "pen": 1},
            }
        )
    )
    items = core.deposit(
        archive.path, [src], dtype=dtype, auth=registry.auth, metadata={"site": 2}
    )
    assert list(items) == [{"source": src, "id": name}]
    record = core.describe(registry.url, name)
    assert (record["experimenter"], record["animal"]) == (user, animal)
    assert (record["metadata"]["pen"], record["metadata"]["site"]) == (1, 2)


def test_deposit_unknown_animal_is_refused(
    core_fields, registry, archive, dtype, tmp_path, unique
):
    name = unique("res")
    src = write_file(tmp_path / f"{name}.txt", name)
    [item] = core.deposit(
        archive.path,
        [src],
        dtype=dtype,
        auth=registry.auth,
        skip_errors=True,
        metadata={"animal": unique("no-such-animal")},
    )
    assert "no such animal" in item["error"]
    assert src.exists()
    assert core.describe(registry.url, name) is None


def test_deposit_dry_run(registry, archive, dtype, tmp_path, unique):
    name = unique("res")
    src = write_file(tmp_path / f"{name}.txt", name)
    items = list(
        core.deposit(archive.path, [src], dtype=dtype, auth=registry.auth, dry_run=True)
    )
    assert items == [{"source": src, "id": name, "dry_run": True, "metadata": {}}]
    assert src.exists()  # source is untouched
    assert core.describe(registry.url, name) is None  # nothing registered
    assert not nbank_archive.resource_path(archive.config, name).exists()


def test_deposit_dry_run_asks_the_registry(
    registry, make_archive, dtype, register, tmp_path, unique
):
    archive = make_archive(require_hash=True)
    taken = register()["name"]
    names = [taken, unique("res"), unique("res"), unique("res"), unique("res")]
    files = [write_file(tmp_path / f"{name}.txt", name) for name in names]
    # the same contents as the file before it
    files[2].write_text(names[1])
    items = list(
        core.deposit(
            archive.path,
            files[:4],
            dtype=dtype,
            auth=registry.auth,
            dry_run=True,
            skip_errors=True,
        )
    )
    assert "name" in items[0]["error"]
    assert items[1]["id"] == names[1]
    assert "the same as resource 1 in this request" in items[2]["error"]
    assert items[3]["id"] == names[3]
    [refused] = core.deposit(
        archive.path,
        files[4:],
        dtype=dtype,
        auth=registry.auth,
        dry_run=True,
        skip_errors=True,
        metadata={"experimenter": "no-such-user"},
    )
    assert "experimenter" in refused["error"]
    assert all(core.describe(registry.url, name) is None for name in names[1:])


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


def test_deposit_refuses_directories(registry, make_archive, dtype, tmp_path, unique):
    archive = make_archive(require_hash=False)
    name = unique("res")
    src = tmp_path / name
    src.mkdir()
    write_file(src / "a.txt", "first")
    [item] = core.deposit(
        archive.path, [src], dtype=dtype, auth=registry.auth, skip_errors=True
    )
    assert "is a directory" in item["error"]
    assert core.describe(registry.url, name) is None
    assert (src / "a.txt").exists()


def test_deposit_auto_id(registry, make_archive, dtype, tmp_path, unique, location_key):
    archive = make_archive(require_hash=False, auto_identifiers=True)
    stem = unique("res")
    src = write_file(tmp_path / f"{stem}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    assert re.fullmatch(r"[0-9a-z]{8}", name)
    assert core.describe(registry.url, name) is not None
    assert nbank_archive.resource_path(archive.config, f"{name}.txt").exists()
    assert location_key(name, archive.name) == f"{name}.txt"


def test_deposit_auto_id_uuid(registry, make_archive, dtype, tmp_path, unique):
    archive = make_archive(
        require_hash=False, auto_identifiers=True, auto_id_type="uuid"
    )
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    assert str(uuid.UUID(name)) == name
    assert core.describe(registry.url, name) is not None


def test_deposit_duplicate_name(registry, archive, dtype, tmp_path, unique):
    # found in the archive, before anything is registered
    name = unique("res")
    first = write_file(tmp_path / f"{name}.txt", "first")
    deposit_one(registry, archive, dtype, first)
    second = write_file(tmp_path / f"{name}.dat", "second")
    with pytest.raises(FileExistsError, match="already stored"):
        deposit_one(registry, archive, dtype, second)
    assert second.exists()
    stored = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    assert stored.read_text() == "first"
    assert [r["name"] for r in core.search(registry.url, name=name)] == [name]


def test_deposit_duplicate_name_skipped(registry, archive, dtype, tmp_path, unique):
    name = unique("res")
    deposit_one(registry, archive, dtype, write_file(tmp_path / f"{name}.txt", "first"))
    second = write_file(tmp_path / f"{name}.dat", "second")
    [item] = core.deposit(
        archive.path, [second], dtype=dtype, auth=registry.auth, skip_errors=True
    )
    assert item["source"] == second
    assert "already stored" in item["error"]


def test_deposit_duplicate_name_in_another_archive(
    registry, archive, make_archive, dtype, tmp_path, unique
):
    # the registry refuses it, as only the other archive has the file
    name = unique("res")
    deposit_one(registry, archive, dtype, write_file(tmp_path / f"{name}.txt", "first"))
    other = make_archive()
    second = write_file(tmp_path / f"{name}.dat", "second")
    with pytest.raises(httpx.HTTPStatusError) as err:
        deposit_one(registry, other, dtype, second)
    assert err.value.response.status_code == 400
    assert "name" in err.value.response.json()
    assert second.exists()
    with pytest.raises(FileNotFoundError):
        nbank_archive.resource_path(other.config, name, resolve_ext=True)


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
    # caught by deposit's pre-flight check, before any file is touched
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    with pytest.raises(RuntimeError, match="no-such-dtype"):
        deposit_one(registry, archive, "no-such-dtype", src)
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


def test_search_by_core_fields(core_fields, registry, register, dtype):
    p50, p51 = core_fields
    user = registry.auth[0]
    a = register(animal=p50, experimenter=user)["name"]
    b = register(animal=p51)["name"]

    def search(**params):
        return {r["name"] for r in core.search(registry.url, dtype=dtype, **params)}

    assert search(animal=p50) == {a}
    assert search(animal__neq=p50) == {b}
    assert search(experimenter=user) == {a}
    assert search(experimenter__isnull="true") == {b}


def test_update_core_fields(core_fields, registry, register):
    animal, _ = core_fields
    name = register(animal=animal, pen=1)["name"]
    user = registry.auth[0]
    [record] = core.update(
        registry.url,
        name,
        auth=registry.auth,
        metadata={"animal": None, "experimenter": user, "protocol": "chorus"},
    )
    assert (record["animal"], record["experimenter"]) == (None, user)
    assert record["metadata"]["protocol"] == "chorus"
    assert record["metadata"]["pen"] == 1
    assert core.describe(registry.url, name)["experimenter"] == user


def test_search_is_paginated(registry, client, register, dtype):
    names = [register()["name"] for _ in range(12)]
    url, _ = reg.find_resource(registry.url)
    r = client.get(url, params={"dtype": dtype})
    assert "next" in r.links
    found = [r["name"] for r in core.search(registry.url, dtype=dtype)]
    assert len(found) == len(set(found))
    assert set(found) == set(names)


def test_find_resources(registry, archive, dtype, register, tmp_path, unique):
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    name = deposit_one(registry, archive, dtype, src)
    stored = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    no_locations = register()["name"]
    local_dir = tmp_path / "dataset"
    local_dir.mkdir()
    in_dir = write_file(local_dir / f"{unique('res')}.txt", "elsewhere")
    results = dict(
        core.find_resources(
            registry.url,
            name,
            no_locations,
            "no-such-resource",
            in_dir.stem,
            local_dir=local_dir,
            verify=True,
        )
    )
    assert results[name] == stored
    assert results[in_dir.stem] == in_dir
    assert isinstance(results[no_locations], FileNotFoundError)
    assert isinstance(results["no-such-resource"], FileNotFoundError)


def test_find_resources_skips_unreachable_locations(
    registry, client, register, make_archive, tmp_path, unique
):
    # the registry lists local archives first, so the unreachable one is first
    unreachable = make_archive()
    reachable = make_archive(accessibility="remote")
    name = register(archive=unreachable.name)["name"]
    url, body = reg.add_location(registry.url, name, reachable.name)
    client.post(url, json=body).raise_for_status()
    src = write_file(tmp_path / f"{unique('res')}.txt", "contents")
    stored = nbank_archive.store_resource(reachable.config, src, id=name)
    assert core.find_resource(registry.url, name) == stored


def test_find_resources_only_unreachable_locations(registry, register, archive):
    name = register(archive=archive.name)["name"]
    with pytest.raises(FileNotFoundError, match="is registered"):
        core.find_resource(registry.url, name)


def test_verify_by_hash(registry, register, tmp_path, unique):
    src = write_file(tmp_path / "file.txt", unique("contents"))
    name = register(sha1=util.hash(src))["name"]
    assert [r["name"] for r in core.verify(registry.url, src)] == [name]
    other = write_file(tmp_path / "other.txt", unique("contents"))
    assert list(core.verify(registry.url, other)) == []


def test_verify_by_id(registry, register, tmp_path, unique):
    src = write_file(tmp_path / "file.txt", unique("contents"))
    name = register(sha1=util.hash(src))["name"]
    assert core.verify(registry.url, src, id=name) is True
    other = write_file(tmp_path / "other.txt", unique("contents"))
    assert core.verify(registry.url, other, id=name) is False


def test_verify_missing_id(registry, tmp_path, unique):
    src = write_file(tmp_path / "file.txt", "contents")
    with pytest.raises(ValueError):
        core.verify(registry.url, src, id=unique("missing"))


def test_update_metadata(registry, register):
    name = register(a=1, b=2)["name"]
    [result] = core.update(
        registry.url, name, auth=registry.auth, metadata={"b": 3, "c": "new"}
    )
    assert result["metadata"] == {"a": 1, "b": 3, "c": "new"}
    [result] = core.update(registry.url, name, auth=registry.auth, metadata={"a": None})
    assert result["metadata"] == {"b": 3, "c": "new"}
    assert core.describe(registry.url, name)["metadata"] == result["metadata"]


def test_update_several(registry, register):
    names = [register()["name"] for _ in range(2)]
    results = list(
        core.update(registry.url, *names, auth=registry.auth, metadata={"k": "v"})
    )
    assert [r["name"] for r in results] == names
    assert all(r["metadata"] == {"k": "v"} for r in results)


def test_update_missing(registry, register, unique):
    missing = unique("missing")
    name = register()["name"]
    results = list(
        core.update(
            registry.url, missing, name, auth=registry.auth, metadata={"k": "v"}
        )
    )
    assert results[0] == {"name": missing, "error": "not found"}
    assert results[1]["name"] == name


def test_location_key(client, registry, register, unique):
    tape = unique("tape")
    url, body = reg.add_archive(registry.url, tape, "tape", f"{tape}:1")
    client.post(url, json=body).raise_for_status()
    name = register()["name"]
    member = f"home/data/archive/resources/{name[:2]}/{name}.wav"

    url, body = reg.add_location(registry.url, name, tape, key=member)
    r = client.post(url, json=body)
    assert r.status_code == 201
    assert r.json()["key"] == member

    url, params = reg.get_locations(registry.url, name)
    [location] = client.get(url, params=params).json()
    assert util.parse_location(location).member == member

    url, body = reg.update_location(registry.url, name, tape, "other.wav")
    assert client.patch(url, json=body).json()["key"] == "other.wav"
    url, body = reg.update_location(registry.url, name, tape, None)
    assert client.patch(url, json=body).json()["key"] is None
