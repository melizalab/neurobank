# -*- mode: python -*-
import json
import os

import httpx
import pytest
import respx

from nbank import archive, transfer, util
from test.test_registry import base_url, bulk_url

bulk_locations_url = bulk_url + "locations/"


@pytest.fixture
def tmp_archive(tmp_path):
    return archive.create(tmp_path / "archive", base_url, allow_directories=True)


def store(cfg, tmp_path, name, contents="contents"):
    src = tmp_path / name
    src.write_text(contents)
    sha1 = util.hash(src)
    return archive.store_resource(cfg, src, id=name), sha1


def location(archive_name, root, name, scheme="neurobank"):
    return {
        "archive_name": archive_name,
        "scheme": scheme,
        "root": str(root),
        "resource_name": name,
    }


def record(name, sha1, *locations):
    return {"name": name, "sha1": sha1, "filename": name, "locations": list(locations)}


class FakeBulkLocations:
    """Answers bulk location requests from a dict of records, like the registry."""

    def __init__(self, respx_mock, records):
        self.records = {r["name"]: r for r in records}
        self.requests = []
        respx_mock.post(bulk_locations_url).mock(side_effect=self.respond)

    def respond(self, request):
        query = json.loads(request.content)
        self.requests.append(query)
        archive_name = query.get("archive")
        lines = []
        for name in query["names"]:
            rec = self.records.get(name)
            if rec is None:
                continue
            locs = [
                loc
                for loc in rec["locations"]
                if archive_name is None or loc["archive_name"] == archive_name
            ]
            if locs:
                lines.append(json.dumps({**rec, "locations": locs}) + "\n")
        return httpx.Response(200, content="".join(lines).encode())


def find(ids, **kwargs):
    with httpx.Client() as session:
        return list(transfer.find_sources(session, base_url, ids, **kwargs))


@respx.mock(assert_all_mocked=True)
def test_find_local_copy(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [record("res_1", sha1, location("arch", tmp_archive["path"], "res_1"))],
    )
    [source] = find(["res_1"])
    assert source == transfer.Source(
        "res_1", sha1=sha1, filename="res_1", path=path, archive="arch"
    )
    assert source.ok


@respx.mock(assert_all_mocked=True)
def test_find_skips_locations_not_on_this_host(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [
            record(
                "res_1",
                sha1,
                location("tape", "tape01:1", "res_1", scheme="tape"),
                location("elsewhere", tmp_path / "not-mounted", "res_1"),
                location("arch", tmp_archive["path"], "res_1"),
            )
        ],
    )
    [source] = find(["res_1"])
    assert (source.path, source.archive) == (path, "arch")


@respx.mock(assert_all_mocked=True)
def test_find_skips_download_location(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [
            record(
                "res_1",
                sha1,
                location(
                    "registry", "localhost:8000/neurobank/download", "res_1", "https"
                ),
                location("arch", tmp_archive["path"], "res_1"),
            )
        ],
    )
    [source] = find(["res_1"])
    assert (source.path, source.archive) == (path, "arch")


@respx.mock(assert_all_mocked=True)
def test_find_no_copy_on_this_host(respx_mock, tmp_path):
    FakeBulkLocations(
        respx_mock,
        [record("res_1", None, location("elsewhere", tmp_path / "gone", "res_1"))],
    )
    [source] = find(["res_1"])
    assert not source.ok
    assert source.error == "no copy on this host"


@respx.mock(assert_all_mocked=True)
def test_find_unregistered(respx_mock):
    FakeBulkLocations(respx_mock, [])
    [source] = find(["missing"])
    assert source == transfer.Source(
        "missing", error="not in the registry, or has no locations"
    )


@respx.mock(assert_all_mocked=True)
def test_find_in_archive(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    _, other_sha1 = store(tmp_archive, tmp_path, "res_2")
    fake = FakeBulkLocations(
        respx_mock,
        [
            record("res_1", sha1, location("arch", tmp_archive["path"], "res_1")),
            record(
                "res_2", other_sha1, location("other", tmp_archive["path"], "res_2")
            ),
        ],
    )
    sources = find(["res_1", "res_2"], archive="arch")
    assert fake.requests[0]["archive"] == "arch"
    assert sources[0].path == path
    assert sources[1].error == "not in archive 'arch'"


@pytest.mark.skipif(os.getuid() == 0, reason="root can read anything")
@respx.mock(assert_all_mocked=True)
def test_find_unreadable(respx_mock, tmp_archive, tmp_path):
    path, sha1 = store(tmp_archive, tmp_path, "res_1")
    FakeBulkLocations(
        respx_mock,
        [record("res_1", sha1, location("arch", tmp_archive["path"], "res_1"))],
    )
    path.chmod(0o000)
    try:
        [source] = find(["res_1"])
    finally:
        path.chmod(0o444)
    assert not source.ok
    assert source.error == f"'{path}' is not readable"


@pytest.mark.skipif(os.getuid() == 0, reason="root can read anything")
@respx.mock(assert_all_mocked=True)
def test_find_directory_with_unreadable_contents(respx_mock, tmp_archive, tmp_path):
    src = tmp_path / "res_1"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_text("contents")
    path = archive.store_resource(tmp_archive, src)
    FakeBulkLocations(
        respx_mock,
        [record("res_1", None, location("arch", tmp_archive["path"], "res_1"))],
    )
    inner = path / "sub" / "data"
    inner.chmod(0o000)
    try:
        [source] = find(["res_1"])
    finally:
        inner.chmod(0o444)
    assert source.error == f"'{inner}' is not readable"


@respx.mock(assert_all_mocked=True)
def test_find_keeps_order_and_drops_duplicates(
    respx_mock, tmp_archive, tmp_path, monkeypatch
):
    monkeypatch.setattr(util, "bulk_batch_size", 2)
    records = []
    for name in ("res_a", "res_b", "res_c"):
        _, sha1 = store(tmp_archive, tmp_path, name, contents=name)
        records.append(record(name, sha1, location("arch", tmp_archive["path"], name)))
    fake = FakeBulkLocations(respx_mock, records)
    sources = find(["res_c", "res_a", "res_c", "missing", "res_b"])
    assert [s.id for s in sources] == ["res_c", "res_a", "missing", "res_b"]
    assert [s.ok for s in sources] == [True, True, False, True]
    # four distinct ids, two per request
    assert [len(q["names"]) for q in fake.requests] == [2, 2]
