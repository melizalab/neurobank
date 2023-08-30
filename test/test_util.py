# -*- coding: utf-8 -*-
# -*- mode: python -*-
from pathlib import Path

import httpx
import pytest
import respx

from nbank import util

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


def test_hash_directory(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    p = d / "hello.txt"
    p.write_text("blarg1")
    hash1 = util.hash_directory(d)
    p = d / "hello2.txt"
    p.write_text("blarg2")
    hash2 = util.hash_directory(d)
    assert hash1 != hash2


def test_parse_neurobank_location():
    from nbank.archive import resource_path

    location = {
        "scheme": "neurobank",
        "root": "/home/data/starlings",
        "resource_name": "dummy",
    }
    path = util.parse_location(location)
    assert path == resource_path(location["root"], location["resource_name"])
    path = util.parse_location(location, "/scratch/")
    assert path == resource_path("/scratch/starlings/", location["resource_name"])


def test_parse_http_location():
    location = {
        "scheme": "https",
        "root": "localhost:8000/bucket",
        "resource_name": "dummy",
    }
    path = util.parse_location(location)
    assert path == "https://localhost:8000/bucket/dummy/"


def test_parse_http_location_strip_slash():
    location = {
        "scheme": "https",
        "root": "localhost:8000/bucket/",
        "resource_name": "dummy",
    }
    path = util.parse_location(location)
    assert path == "https://localhost:8000/bucket/dummy/"


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
    mocked_api.post(url).respond(200, json=data)
    result = util.query_registry_bulk(httpx, url, {"names": "one"})
    assert result == data


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


def test_download(mocked_api, tmp_path):
    url = "https://meliza.org/neurobank/resources/dummy/download"
    content = str(dummy_info)
    p = tmp_path / "output"
    mocked_api.get(url).respond(content=content)
    util.download_to_file(httpx, url, p)
    assert p.read_text() == content
