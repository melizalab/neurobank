# -*- mode: python -*-
import json
from pathlib import Path

import httpx
import pytest
import respx

from nbank import tape_archive, util

dummy_info = {"name": "django-neurobank", "version": "0.10.11", "api_version": "1.0"}


@pytest.fixture
def mocked_api():
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as respx_mock:
        yield respx_mock


def test_id_from_str_fname():
    test = "/home/data/archive/resources/re/resource.wav"
    assert util.id_from_fname(test) == "resource"


def test_id_from_path_fname():
    test = Path("/a/random/directory/resource.wav")
    assert util.id_from_fname(test) == "resource"


def test_id_from_invalid_fname():
    test = "/a/file/with/bad/char%ct@rs"
    with pytest.raises(ValueError):
        _ = util.id_from_fname(test)


# names whose order differs when sorted by path component rather than as strings
directory_files = {
    "a.txt": "first",
    "a/b": "nested",
    "a-b": "dash",
    "a/c/d.bin": "deeper",
    "B": "upper",
    "z": "",
}


def make_directory(root):
    for rel, contents in directory_files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    (root / "empty_dir").mkdir()
    return root


def test_hash_directory_is_unchanged(tmp_path):
    # registered hashes of existing directory resources depend on this format,
    # so these values must not change
    root = make_directory(tmp_path / "res")
    assert util.hash_directory(root) == "c080a1a8c60ebd275493b8e90bd7f0c90624e798"
    assert util.hash_directory(root, "md5") == "99d607a5053c7272e12c9a91cba4ee0c"


def test_directory_hasher_matches_hash_directory(tmp_path):
    import io

    root = make_directory(tmp_path / "res")
    hasher = util.DirectoryHasher()
    for rel in reversed(list(directory_files)):
        hasher.add_stream(rel, io.BytesIO(directory_files[rel].encode()))
    assert hasher.hexdigest() == util.hash_directory(root)


def test_directory_hasher_rejects_repeated_path():
    import io

    hasher = util.DirectoryHasher()
    hasher.add_stream("a/b", io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="a/b"):
        hasher.add_stream("a/b", io.BytesIO(b"y"))


def test_hash_stream_reads_in_blocks_and_copies(tmp_path, monkeypatch):
    import hashlib
    import io

    monkeypatch.setattr(util, "_hash_block_size", 7)
    data = bytes(range(256)) * 3
    copy = io.BytesIO()
    digest = util.hash_stream(io.BytesIO(data), copy_to=copy)
    assert digest == hashlib.sha1(data).hexdigest()
    assert copy.getvalue() == data
    src = tmp_path / "data"
    src.write_bytes(data)
    assert util.hash(src) == digest


def test_hash_directory_with_multiple_files(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    (d / "hello.txt").write_text("blarg1")
    (d / "hello2.txt").write_text("blarg2")
    hash1 = util.hash_directory(d)
    hash2 = util.hash_directory(d)
    assert hash1 == hash2


def test_hash_directory_after_moving(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    (d / "hello.txt").write_text("blarg1")
    (d / "hello2.txt").write_text("blarg2")
    hash1 = util.hash_directory(d)
    # rename the directory to simulate depositing it
    new_d = d.rename(tmp_path / "new_sub")
    hash2 = util.hash_directory(new_d)
    assert hash1 == hash2


def test_hash_directory_detects_extra_file(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    p = d / "hello.txt"
    p.write_text("blarg1")
    hash1 = util.hash_directory(d)
    p = d / "hello2.txt"
    p.write_text("blarg2")
    hash2 = util.hash_directory(d)
    assert hash1 != hash2


def test_hash_directory_detects_missing_file(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    p = d / "hello.txt"
    p.write_text("blarg1")
    p = d / "hello2.txt"
    p.write_text("blarg2")
    hash1 = util.hash_directory(d)
    p.unlink()
    hash2 = util.hash_directory(d)
    assert hash1 != hash2


def test_hash_directory_detects_modified_file(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    p = d / "hello.txt"
    p.write_text("blarg1")
    p = d / "hello2.txt"
    p.write_text("blarg2")
    hash1 = util.hash_directory(d)
    p.write_text("abcedf")
    hash2 = util.hash_directory(d)
    assert hash1 != hash2


def test_parse_http_location():
    location = {
        "scheme": "https",
        "root": "localhost:8000/bucket",
        "resource_name": "dummy",
    }
    res = util.parse_location(location)
    assert isinstance(res, util.HttpResource)
    assert res.url == "https://localhost:8000/bucket/dummy/"


def test_parse_http_location_strip_slash():
    location = {
        "scheme": "https",
        "root": "localhost:8000/bucket/",
        "resource_name": "dummy",
    }
    res = util.parse_location(location)
    assert isinstance(res, util.HttpResource)
    assert res.url == "https://localhost:8000/bucket/dummy/"


def test_parse_tape_location():
    location = {"scheme": "tape", "root": "tape01:3", "resource_name": "dummy"}
    res = util.parse_location(location)
    assert isinstance(res, tape_archive.Resource)
    assert (res.tape_name, res.file_index, res.id) == ("tape01", 3, "dummy")
    assert res.member is None
    assert res.local is False


def test_parse_tape_location_with_key():
    location = {
        "scheme": "tape",
        "root": "tape01:3",
        "resource_name": "dummy",
        "key": "home/data/archive/resources/du/dummy.wav",
    }
    res = util.parse_location(location)
    assert res.member == "home/data/archive/resources/du/dummy.wav"


def test_parse_http_location_ignores_key():
    location = {
        "scheme": "https",
        "root": "localhost:8000/bucket",
        "resource_name": "dummy",
        "key": "something",
    }
    res = util.parse_location(location)
    assert res.url == "https://localhost:8000/bucket/dummy/"


def test_parse_unreachable_neurobank_location(tmp_path):
    location = {
        "scheme": "neurobank",
        "root": str(tmp_path / "not-mounted"),
        "resource_name": "dummy",
    }
    assert util.parse_location(location) is None


def test_location_schemes_are_registered():
    from nbank import archive, types

    assert types.location_class("neurobank") is archive.Resource
    assert types.location_class("tape") is tape_archive.Resource
    assert types.location_class("http") is util.HttpResource
    assert types.location_class("https") is util.HttpResource


def test_location_schemes_register_when_util_is_imported():
    # a fresh interpreter, so no other module has already imported the classes
    import subprocess
    import sys

    code = (
        "from nbank import util; "
        "print(util.parse_location("
        "{'scheme': 'tape', 'root': 't:1', 'resource_name': 'x'}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "tape://t:1/x"


def test_register_new_location_scheme(monkeypatch):
    from nbank import types

    monkeypatch.setattr(types, "_location_schemes", dict(types._location_schemes))

    @types.location_scheme
    class DummyResource:
        schemes = ("dummy",)

        def __init__(self, id):
            self.id = id

        @classmethod
        def from_location(cls, location, *, alt_base=None, http_session=None):
            return cls(location["resource_name"])

    location = {"scheme": "dummy", "root": "somewhere", "resource_name": "res_1"}
    res = util.parse_location(location)
    assert isinstance(res, DummyResource)
    assert res.id == "res_1"


def test_register_duplicate_location_scheme(monkeypatch):
    from nbank import types

    monkeypatch.setattr(types, "_location_schemes", dict(types._location_schemes))

    class Impostor:
        schemes = ("tape",)

    with pytest.raises(ValueError, match="tape"):
        types.location_scheme(Impostor)
    assert types.location_class("tape") is tape_archive.Resource


def test_parse_unknown_scheme():
    location = {"scheme": "ipfs", "root": "gateway", "resource_name": "dummy"}
    assert util.parse_location(location) is None


def test_query_registry(mocked_api):
    url = "https://meliza.org/neurobank/info/"
    mocked_api.get(url).respond(200, json=dummy_info)
    data = util.query_registry(httpx, url)
    assert data == dummy_info


def test_query_registry_invalid(mocked_api):
    url = "https://meliza.org/neurobank/bad/"
    mocked_api.get(url).respond(404, json={"detail": "not found"})
    data = util.query_registry(httpx, url)
    assert data is None


def test_query_registry_error(mocked_api):
    url = "https://meliza.org/neurobank/bad/"
    mocked_api.get(url).respond(400, json={"error": "bad request"})
    with pytest.raises(httpx.HTTPStatusError):
        _ = util.query_registry(httpx, url)


def test_query_params(mocked_api):
    url = "https://meliza.org/neurobank/resources/"
    params = {"experimenter": "dmeliza"}
    mocked_api.get(url, params=params).respond(200, json=dummy_info)
    data = util.query_registry(httpx, url, params)
    assert data == dummy_info


def test_query_paginated(mocked_api):
    url = "https://meliza.org/neurobank/resources/"
    params = {"experimenter": "dmeliza"}
    data = [{"first": "one"}, {"second": "one"}]
    mocked_api.get(url, params=params).respond(200, json=data)
    for i, result in enumerate(util.query_registry_paginated(httpx, url, params)):
        assert result == data[i]


def test_query_first(mocked_api):
    url = "https://meliza.org/neurobank/resources/"
    data = [{"item": "one"}]
    mocked_api.get(url).respond(200, json=data)
    result = util.query_registry_first(httpx, url)
    assert result == data[0]


def test_query_bulk(mocked_api):
    url = "https://meliza.org/neurobank/bulk/resources"
    data = [{"item": "one"}]
    stream = (json.dumps(item).encode() for item in data)
    mocked_api.post(url).respond(200, stream=stream)
    result = list(util.query_registry_bulk(httpx, url, {"names": "one"}))
    assert result == data


def test_query_bulk_error_body_is_readable(mocked_api):
    url = "https://meliza.org/neurobank/bulk/resources"
    body = {"detail": "must supply at least one name"}
    mocked_api.post(url).respond(400, stream=[json.dumps(body).encode()])
    with pytest.raises(httpx.HTTPStatusError) as err:
        list(util.query_registry_bulk(httpx, url, {"names": []}))
    assert err.value.response.json() == body


def test_query_first_empty(mocked_api):
    url = "https://meliza.org/neurobank/resources/"
    data = []
    mocked_api.get(url).respond(json=data)
    result = util.query_registry_first(httpx, url)
    assert result is None


def test_query_first_invalid(mocked_api):
    url = "https://meliza.org/neurobank/bad/"
    mocked_api.get(url).respond(404, json={"detail": "not found"})
    with pytest.raises(httpx.HTTPStatusError):
        _ = util.query_registry_first(httpx, url)


def test_fetch(mocked_api, tmp_path):
    url = "https://meliza.org/neurobank/download/dummy/"
    content = str(dummy_info)
    p = tmp_path / "output"
    resource = util.HttpResource(
        {
            "scheme": "https",
            "root": "meliza.org/neurobank/download",
            "resource_name": "dummy",
        },
        httpx,
    )
    assert resource.url == url
    mocked_api.get(url).respond(content=content)
    resource.fetch(p)
    assert p.read_text() == content


def test_fetch_error_body_is_readable(mocked_api, tmp_path):
    url = "https://meliza.org/neurobank/download/dummy/"
    body = {"detail": "not found"}
    p = tmp_path / "output"
    resource = util.HttpResource(
        {
            "scheme": "https",
            "root": "meliza.org/neurobank/download",
            "resource_name": "dummy",
        },
        httpx,
    )
    mocked_api.get(url).respond(404, stream=[json.dumps(body).encode()])
    with pytest.raises(httpx.HTTPStatusError) as err:
        resource.fetch(p)
    assert err.value.response.json() == body
    assert not p.exists()
