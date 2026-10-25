# -*- mode: python -*-
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from nbank import archive, cache, core
from test.test_registry import base_url, bulk_url

locations_url = f"{bulk_url}locations/"
download_url = "https://localhost:8000/neurobank/download/"
contents = b"some data"
sha1 = hashlib.sha1(contents).hexdigest()


@pytest.fixture(autouse=True)
def nbank_cache(tmp_path, monkeypatch):
    path = tmp_path / "cache"
    monkeypatch.setenv("NBANK_CACHE", str(path))
    return path


def http_location(id):
    return {
        "scheme": "https",
        "root": "localhost:8000/neurobank/download",
        "resource_name": id,
    }


def archive_location(cfg, id):
    return {
        "scheme": "neurobank",
        "root": str(cfg.path),
        "resource_name": id,
        "key": f"{id}.txt",
    }


def record(id, *locations, sha1=sha1):
    return {"name": id, "sha1": sha1, "filename": f"{id}.txt", "locations": locations}


def respond_with(mocked_api, *records):
    return mocked_api.post(locations_url).respond(
        text="\n".join(json.dumps(r) for r in records)
    )


def store(cfg, id, data=contents) -> Path:
    path = archive.resource_path(cfg, id).with_suffix(".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def find(*ids, **kwargs):
    return dict(core.find_resources(base_url, *ids, **kwargs))


def test_local_dir_needs_no_registry(mocked_api, tmp_path):
    (tmp_path / "res_1.wav").write_bytes(contents)
    assert find("res_1", local_dir=tmp_path) == {"res_1": tmp_path / "res_1.wav"}


def test_local_archive(mocked_api, tmp_archive):
    path = store(tmp_archive, "res_1")
    respond_with(
        mocked_api,
        record("res_1", archive_location(tmp_archive, "res_1"), http_location("res_1")),
    )
    assert find("res_1") == {"res_1": path}


@pytest.fixture
def other_scheme(monkeypatch):
    """Registers a scheme whose resources are downloaded from another host (like a
    public repository), and returns the files it has served."""
    from nbank import types

    monkeypatch.setattr(types, "_location_schemes", dict(types._location_schemes))
    served = []

    @types.location_scheme
    class Repository:
        schemes = ("repository",)

        def __init__(self, location):
            self.host = location["root"]

        @classmethod
        def from_location(cls, location, context):
            return cls(location)

        def fetch(self, target):
            target.write_bytes(contents)
            served.append(target)
            return target

    return served


def test_other_remote_scheme_downloads_into_cache(
    mocked_api, nbank_cache, other_scheme
):
    location = {
        "scheme": "repository",
        "root": "repo.org:8080",
        "resource_name": "res_1",
    }
    respond_with(mocked_api, record("res_1", location))
    path = find("res_1")["res_1"]
    assert path == nbank_cache / cache.host_dir("repo.org:8080") / "res_1.txt"
    assert path.read_bytes() == contents
    # cached after the first download
    respond_with(mocked_api, record("res_1", location))
    assert find("res_1")["res_1"] == path
    assert len(other_scheme) == 1


def test_download_into_cache(mocked_api, nbank_cache):
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    download = mocked_api.get(f"{download_url}res_1/").respond(content=contents)
    path = find("res_1")["res_1"]
    assert path == nbank_cache / "localhost_8000" / "res_1.txt"
    assert path.read_bytes() == contents
    # the second time it's in the cache
    assert find("res_1") == {"res_1": path}
    assert download.call_count == 1
    assert list(path.parent.iterdir()) == [path]


def test_without_download(mocked_api):
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    result = find("res_1", download=False)["res_1"]
    assert isinstance(result, FileNotFoundError)


def test_failed_download_leaves_nothing(mocked_api, nbank_cache):
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    mocked_api.get(f"{download_url}res_1/").respond(500)
    assert isinstance(find("res_1")["res_1"], FileNotFoundError)
    assert list((nbank_cache / "localhost_8000").iterdir()) == []


def test_unregistered(mocked_api, tmp_archive):
    path = store(tmp_archive, "res_1")
    respond_with(mocked_api, record("res_1", archive_location(tmp_archive, "res_1")))
    results = find("res_1", "res_2")
    assert results["res_1"] == path
    assert "isn't in the registry" in str(results["res_2"])


def test_unreachable(mocked_api, tmp_path):
    missing = {
        "scheme": "neurobank",
        "root": str(tmp_path),
        "resource_name": "res_1",
        "key": "res_1.txt",
    }
    tape = {"scheme": "tape", "root": "tape01:3", "resource_name": "res_1"}
    respond_with(mocked_api, record("res_1", missing, tape))
    assert isinstance(find("res_1")["res_1"], FileNotFoundError)


def test_verify_skips_a_bad_copy(mocked_api, tmp_archive):
    store(tmp_archive, "res_1", b"corrupted")
    respond_with(
        mocked_api,
        record("res_1", archive_location(tmp_archive, "res_1"), http_location("res_1")),
    )
    mocked_api.get(f"{download_url}res_1/").respond(content=contents)
    path = find("res_1", verify=True)["res_1"]
    assert path.read_bytes() == contents


def test_verify_replaces_a_bad_cached_file(mocked_api, nbank_cache):
    cached = nbank_cache / "localhost_8000" / "res_1.txt"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"corrupted")
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    mocked_api.get(f"{download_url}res_1/").respond(content=contents)
    assert find("res_1")["res_1"].read_bytes() == b"corrupted"
    assert find("res_1", verify=True)["res_1"].read_bytes() == contents


def test_verify_refuses_a_bad_download(mocked_api, nbank_cache):
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    mocked_api.get(f"{download_url}res_1/").respond(content=b"corrupted")
    assert isinstance(find("res_1", verify=True)["res_1"], FileNotFoundError)
    assert list((nbank_cache / "localhost_8000").iterdir()) == []


def test_requests_are_batched(mocked_api, monkeypatch, tmp_archive):
    from nbank import util

    monkeypatch.setattr(util, "bulk_batch_size", 2)
    paths = {id: store(tmp_archive, id) for id in ("res_1", "res_2", "res_3")}
    route = mocked_api.post(locations_url)
    route.side_effect = lambda request: httpx.Response(
        200,
        text="\n".join(
            json.dumps(record(id, archive_location(tmp_archive, id)))
            for id in json.loads(request.content)["names"]
        ),
    )
    assert find(*paths) == paths
    assert route.call_count == 2


def test_find_resource(mocked_api, tmp_archive):
    path = store(tmp_archive, "res_1")
    route = respond_with(
        mocked_api, record("res_1", archive_location(tmp_archive, "res_1"))
    )
    assert core.find_resource(base_url, "res_1") == path
    route.respond(text="")
    with pytest.raises(FileNotFoundError):
        core.find_resource(base_url, "res_2")


def test_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NBANK_CACHE", str(tmp_path))
    assert cache.cache_dir() == tmp_path
    monkeypatch.delenv("NBANK_CACHE")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache.cache_dir() == tmp_path / "xdg" / "neurobank"


def test_cache_host_dir():
    assert cache.host_dir("localhost:8000") == "localhost_8000"
    assert cache.host_dir("localhost") == "localhost"


class CutOff(httpx.SyncByteStream):
    def __iter__(self):
        yield contents[:4]
        raise httpx.ReadError("connection lost")


def test_interrupted_download_leaves_nothing(mocked_api, nbank_cache):
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    mocked_api.get(f"{download_url}res_1/").mock(
        return_value=httpx.Response(200, stream=CutOff())
    )
    assert isinstance(find("res_1")["res_1"], FileNotFoundError)
    assert list((nbank_cache / "localhost_8000").iterdir()) == []


class Fails(httpx.SyncByteStream):
    def __iter__(self):
        yield contents[:4]
        raise RuntimeError("disk full")


def test_download_that_raises_leaves_nothing(mocked_api, nbank_cache):
    respond_with(mocked_api, record("res_1", http_location("res_1")))
    download = mocked_api.get(f"{download_url}res_1/")
    download.mock(return_value=httpx.Response(200, stream=Fails()))
    with pytest.raises(RuntimeError):
        find("res_1")
    assert list((nbank_cache / "localhost_8000").iterdir()) == []
