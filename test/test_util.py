# -*- coding: utf-8 -*-
# -*- mode: python -*-
import pytest
from pathlib import Path
import responses
from responses import matchers
import requests

from nbank import util

dummy_info = {"name": "django-neurobank", "version": "0.10.11", "api_version": "1.0"}


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


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
    assert path == "https://localhost:8000/bucket/dummy"


@responses.activate
def test_query_registry():
    url = "https://meliza.org/neurobank/info/"
    responses.get(url, json=dummy_info)
    data = util.query_registry(requests, url)
    assert data == dummy_info


@responses.activate
def test_query_registry_invalid():
    url = "https://meliza.org/neurobank/bad/"
    responses.get(url, json={"detail": "not found"}, status=404)
    with pytest.raises(ValueError):
        _ = util.query_registry(requests, url)


@responses.activate
def test_query_params():
    url = "https://meliza.org/neurobank/resources/"
    params = {"experimenter": "dmeliza"}
    responses.get(url, json=dummy_info, match=[matchers.query_param_matcher(params)])
    data = util.query_registry(requests, url, params)
    assert data == dummy_info


@responses.activate
def test_download(tmp_path):
    url = "https://meliza.org/neurobank/resources/dummy/download"
    content = str(dummy_info)
    p = tmp_path / "output"
    responses.get(
        url,
        body=content,
        match=[matchers.request_kwargs_matcher({"stream": True})],
    )
    util.download_to_file(requests, url, p)
    assert p.read_text() == content
