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


def test_hash_stream_reports_progress(monkeypatch):
    import io

    monkeypatch.setattr(util, "_hash_block_size", 10)
    seen = []
    util.hash_stream(io.BytesIO(b"x" * 25), progress=seen.append)
    assert seen == [10, 20, 25]


def test_parse_http_location():
    location = {
        "scheme": "https",
        "root": "localhost:8000/bucket",
        "resource_name": "dummy",
    }
    res = util.parse_location(location)
    assert isinstance(res, util.HttpResource)
    assert res.url == "https://localhost:8000/bucket/dummy/"
    assert (
        repr(res) == "<remote resource: dummy @ https://localhost:8000/bucket/dummy/>"
    )


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
        def from_location(cls, location, context):
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


search_url = "https://meliza.org/neurobank/resources/"
ndjson = {"Content-Type": util.NDJSON}


def ndjson_lines(*records):
    return "".join(json.dumps(r) + "\n" for r in records)


def test_query_stream(mocked_api):
    data = [{"name": "res_1"}, {"name": "res_2"}]
    route = mocked_api.get(search_url, params={"archive": "a"}).respond(
        200, headers=ndjson, text=ndjson_lines(*data)
    )
    with util.registry_session() as session:
        result = list(util.query_registry_stream(session, search_url, {"archive": "a"}))
    assert result == data
    assert route.call_count == 1
    assert route.calls.last.request.headers["Accept"] == (
        f"{util.NDJSON}; version={util.API_VERSION}"
    )


# characters that str.splitlines() takes as line breaks but JSON leaves unescaped
UNESCAPED_BREAKS = "\x85\u2028\u2029"


def test_query_stream_splits_only_at_newlines(mocked_api):
    data = [{"name": "res_1", "metadata": {"note": f"a{UNESCAPED_BREAKS}b"}}]
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
    mocked_api.get(search_url).respond(200, headers=ndjson, text=text)
    with util.registry_session() as session:
        assert list(util.query_registry_stream(session, search_url)) == data


def paged(mocked_api, first_response=None):
    """Mocks a search in two pages; first_response replaces the first request's."""
    page_2 = f"{search_url}?page=2"
    mocked_api.get(page_2).respond(200, json=[{"name": "res_2"}])
    first = httpx.Response(
        200, json=[{"name": "res_1"}], headers={"Link": f'<{page_2}>; rel="next"'}
    )
    responses = iter([first_response, first] if first_response else [first])
    return mocked_api.get(search_url, params={}).mock(
        side_effect=lambda request: next(responses)
    )


def test_query_stream_falls_back_to_pages(mocked_api):
    # a registry that can't stream refuses the request
    route = paged(mocked_api, httpx.Response(406, json={"detail": "not acceptable"}))
    with util.registry_session() as session:
        result = list(util.query_registry_stream(session, search_url))
    assert result == [{"name": "res_1"}, {"name": "res_2"}]
    accepts = [call.request.headers["Accept"] for call in route.calls]
    assert accepts == [
        f"{util.NDJSON}; version={util.API_VERSION}",
        f"application/json; version={util.API_VERSION}",
    ]


def test_query_stream_uses_json_pages_given_instead(mocked_api):
    route = paged(mocked_api)
    with util.registry_session() as session:
        result = list(util.query_registry_stream(session, search_url))
    assert result == [{"name": "res_1"}, {"name": "res_2"}]
    assert route.call_count == 1


# what gunicorn writes into a stream when it kills a worker that ran too long
CUT_OFF = (
    "HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n"
    "<html>\n  <head>\n    <title>Internal Server Error</title>\n"
)


def test_query_stream_cut_off(mocked_api):
    text = ndjson_lines({"name": "res_1"}) + CUT_OFF
    mocked_api.get(search_url).respond(200, headers=ndjson, text=text)
    records = []
    with (
        util.registry_session() as session,
        pytest.raises(util.RegistryStreamError, match="cut off"),
    ):
        records.extend(util.query_registry_stream(session, search_url))
    assert records == [{"name": "res_1"}]


def test_query_stream_error_is_readable(mocked_api):
    errors = {"metadata__animal": ["search it with ?animal="]}
    mocked_api.get(search_url).respond(400, headers=ndjson, text=ndjson_lines(errors))
    with (
        util.registry_session() as session,
        pytest.raises(httpx.HTTPStatusError) as exc_info,
    ):
        list(util.query_registry_stream(session, search_url))
    assert exc_info.value.response.json() == errors


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


def test_query_bulk_splits_only_at_newlines(mocked_api):
    url = "https://meliza.org/neurobank/bulk/resources"
    data = [{"item": f"one{UNESCAPED_BREAKS}"}, {"item": "two"}]
    stream = (json.dumps(item, ensure_ascii=False).encode() + b"\n" for item in data)
    mocked_api.post(url).respond(200, stream=stream)
    assert list(util.query_registry_bulk(httpx, url, {"names": "one"})) == data


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


@pytest.fixture
def unchecked(monkeypatch):
    """Checks registry versions, as outside tests."""
    monkeypatch.setattr(util, "_checked_registries", set())


registry_url = "https://meliza.org/neurobank/"
info_url = f"{registry_url}info/"


def test_registry_session_asks_for_the_api_version(mocked_api):
    route = mocked_api.get(f"{registry_url}resources/").respond(200, json=[])
    with util.registry_session() as session:
        list(util.query_registry_paginated(session, f"{registry_url}resources/"))
    assert route.calls.last.request.headers["Accept"] == (
        f"application/json; version={util.API_VERSION}"
    )


def test_registry_version_is_checked_once(mocked_api, unchecked):
    route = mocked_api.get(info_url).respond(200, json={"api_version": "2.1"})
    for _ in range(2):
        with util.registry_session(registry_url):
            pass
    assert route.call_count == 1


@pytest.mark.parametrize("info", [{"api_version": "1.3"}, {"name": "something"}])
def test_other_registry_versions_are_refused(mocked_api, unchecked, info):
    mocked_api.get(info_url).respond(200, json=info)
    with pytest.raises(util.RegistryVersionError, match=r"needs API 2\.x"):
        with util.registry_session(registry_url):
            pass


def test_queries_use_the_session_auth(mocked_api):
    get = mocked_api.get(info_url).respond(200, json={})
    bulk = mocked_api.post(f"{registry_url}bulk/").respond(200, text="")
    with util.registry_session(registry_url, auth=("user", "pw")) as session:
        util.query_registry(session, info_url)
        list(util.query_registry_bulk(session, f"{registry_url}bulk/", {}))
    for route in (get, bulk):
        assert route.calls.last.request.headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    "url",
    [
        "https://elsewhere.org/files/res_1/",
        # the same host on another port or scheme is another origin
        "https://meliza.org:8443/neurobank/download/res_1/",
        "http://meliza.org/neurobank/download/res_1/",
    ],
)
def test_session_auth_stays_with_the_registry(mocked_api, url):
    route = mocked_api.get(url).respond(200, content=b"contents")
    with util.registry_session(registry_url, auth=("user", "pw")) as session:
        session.get(url)
    assert "Authorization" not in route.calls.last.request.headers


def test_download_from_another_host_has_no_credentials(mocked_api, tmp_path):
    download = mocked_api.get("https://elsewhere.org/files/res_1/").respond(
        200, content=b"contents"
    )
    location = {
        "scheme": "https",
        "root": "elsewhere.org/files",
        "resource_name": "res_1",
    }
    with util.registry_session(registry_url, auth=("user", "pw")) as session:
        util.fetch_resource(session, [location], tmp_path / "res_1")
    assert "Authorization" not in download.calls.last.request.headers


def basic(credentials: str) -> str:
    import base64

    return "Basic " + base64.b64encode(credentials.encode()).decode()


def test_netrc_credentials_go_to_each_host(mocked_api, tmp_path):
    netrc = tmp_path / ".netrc"
    netrc.write_text(
        "machine meliza.org login user password pw\n"
        "machine elsewhere.org login other password secret\n"
    )
    netrc.chmod(0o600)
    query = mocked_api.get(info_url).respond(200, json={})
    download = mocked_api.get("https://elsewhere.org/files/res_1/").respond(
        200, content=b"contents"
    )
    unknown = mocked_api.get("https://unknown.org/files/res_1/").respond(200)
    with util.registry_session(
        registry_url, auth=httpx.NetRCAuth(str(netrc))
    ) as session:
        session.get(info_url)
        session.get("https://elsewhere.org/files/res_1/")
        session.get("https://unknown.org/files/res_1/")
    assert query.calls.last.request.headers["Authorization"] == basic("user:pw")
    assert download.calls.last.request.headers["Authorization"] == basic("other:secret")
    assert "Authorization" not in unknown.calls.last.request.headers


def test_session_auth_needs_the_registry_url():
    with pytest.raises(ValueError, match="registry_url"):
        with util.registry_session(auth=("user", "pw")):
            pass


def test_fetch_resource_verifies_hash(mocked_api, tmp_path):
    import hashlib

    good = b"contents"
    locations = [
        {"scheme": "https", "root": f"meliza.org/{mirror}", "resource_name": "res_1"}
        for mirror in ("first", "second")
    ]
    mocked_api.get("https://meliza.org/first/res_1/").respond(content=b"corrupted")
    mocked_api.get("https://meliza.org/second/res_1/").respond(content=good)
    target = tmp_path / "res_1"
    with httpx.Client() as session:
        result = util.fetch_resource(
            session, locations, target, sha1=hashlib.sha1(good).hexdigest()
        )
        assert result == target
        assert target.read_bytes() == good
        target.unlink()
        result = util.fetch_resource(session, locations[:1], target, sha1="0" * 40)
    assert isinstance(result, util.NotFetchableError)
    assert "registered hash" in str(result)
    assert not target.exists()


def test_base36_id():
    ids = {util.base36_id() for _ in range(100)}
    assert len(ids) == 100
    for id in ids:
        assert len(id) == 8
        assert set(id) <= set("0123456789abcdefghijklmnopqrstuvwxyz")
