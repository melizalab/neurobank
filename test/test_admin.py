# -*- mode: python -*-
import json
import logging
from argparse import Namespace

import pytest
import respx

from nbank import admin
from test.test_registry import base_url, resource_url


@pytest.fixture
def id_file(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text("# comment\nres_1\n\nres_2\n")
    return path


def make_args(id_file, record=None, dry_run=False):
    return Namespace(
        resources=id_file,
        record=record,
        dry_run=dry_run,
        registry_url=base_url,
        auth=None,
    )


def mock_registry(mock, ids):
    records = {}
    for id in ids:
        records[id] = {"name": id, "sha1": "abc", "metadata": {"k": id}}
        mock.get(f"{resource_url}{id}/").respond(json=records[id])
        mock.get(f"{resource_url}{id}/locations/").respond(json=[])
    return records


def test_delete_record(id_file, tmp_path):
    record = tmp_path / "purged.jsonl"
    with respx.mock(assert_all_called=False) as mock:
        records = mock_registry(mock, ["res_1", "res_2"])
        deletes = [mock.delete(f"{resource_url}{i}/").respond(204) for i in records]
        admin.delete_resources(make_args(id_file, record))
        assert all(d.called for d in deletes)
    lines = [json.loads(line) for line in record.read_text().splitlines()]
    assert lines == list(records.values())


def test_delete_record_skips_failed_purge(id_file, tmp_path):
    record = tmp_path / "purged.jsonl"
    with respx.mock(assert_all_called=False) as mock:
        records = mock_registry(mock, ["res_1", "res_2"])
        mock.delete(f"{resource_url}res_1/").respond(404)
        mock.delete(f"{resource_url}res_2/").respond(204)
        admin.delete_resources(make_args(id_file, record))
    lines = [json.loads(line) for line in record.read_text().splitlines()]
    assert lines == [records["res_2"]]


def test_delete_record_dry_run(id_file, tmp_path):
    record = tmp_path / "purged.jsonl"
    with respx.mock(assert_all_called=False) as mock:
        mock_registry(mock, ["res_1", "res_2"])
        admin.delete_resources(make_args(id_file, record, dry_run=True))
    assert record.read_text() == ""


def test_delete_without_record(id_file):
    with respx.mock(assert_all_called=False) as mock:
        mock_registry(mock, ["res_1", "res_2"])
        d = mock.delete(f"{resource_url}res_1/").respond(204)
        mock.delete(f"{resource_url}res_2/").respond(204)
        admin.delete_resources(make_args(id_file))
    assert d.called


# supersede


def supersede_args(*ids, by=None, note=None, clear=False, dry_run=False):
    return Namespace(
        ids=list(ids),
        by=by,
        note=note,
        clear=clear,
        dry_run=dry_run,
        registry_url=base_url,
        auth=None,
    )


def route_superseded(mock, id, superseded_on=None, **details):
    record = {
        "name": id,
        "superseded_on": superseded_on,
        "superseded_by": None,
        "superseded_note": "",
        **details,
    }
    mock.get(f"{resource_url}{id}/").respond(json=record)
    return mock.patch(f"{resource_url}{id}/").mock(
        side_effect=lambda request: respx.MockResponse(
            json={**record, **json.loads(request.content)}
        )
    )


def sent(route):
    return [json.loads(call.request.content) for call in route.calls]


def test_supersede_marks_with_the_time(caplog):
    caplog.set_level(logging.INFO, logger="nbank")
    with respx.mock() as mock:
        patch = route_superseded(mock, "res_1")
        admin.supersede_resources(supersede_args("res_1", by="res_2", note="dup"))
    [data] = sent(patch)
    assert data["superseded_by"] == "res_2" and data["superseded_note"] == "dup"
    assert data["superseded_on"].endswith("+00:00")
    assert "superseded on" in caplog.text and "by res_2 (dup)" in caplog.text


def test_supersede_keeps_the_time_of_a_marked_resource():
    with respx.mock() as mock:
        patch = route_superseded(mock, "res_1", "2026-01-01T00:00:00Z")
        admin.supersede_resources(supersede_args("res_1", note="no data"))
    assert sent(patch) == [{"superseded_note": "no data"}]


def test_supersede_marked_resource_with_nothing_to_change():
    with respx.mock(assert_all_called=False) as mock:
        patch = route_superseded(mock, "res_1", "2026-01-01T00:00:00Z")
        admin.supersede_resources(supersede_args("res_1"))
    assert not patch.called


def test_supersede_clear():
    with respx.mock() as mock:
        patch = route_superseded(mock, "res_1", "2026-01-01T00:00:00Z")
        admin.supersede_resources(supersede_args("res_1", clear=True))
    assert sent(patch) == [{"superseded_on": None}]


def test_supersede_clear_refuses_a_note(caplog):
    with respx.mock(assert_all_called=False) as mock:
        patch = route_superseded(mock, "res_1", "2026-01-01T00:00:00Z")
        admin.supersede_resources(supersede_args("res_1", clear=True, note="x"))
    assert not patch.called
    assert "don't give them" in caplog.text


def test_supersede_dry_run(caplog):
    caplog.set_level(logging.INFO, logger="nbank")
    with respx.mock(assert_all_called=False) as mock:
        patch = route_superseded(mock, "res_1")
        admin.supersede_resources(supersede_args("res_1", by="res_2", dry_run=True))
    assert not patch.called
    assert "would send" in caplog.text


def test_supersede_continues_past_errors(caplog):
    with respx.mock() as mock:
        mock.get(f"{resource_url}res_1/").respond(404)
        bad = route_superseded(mock, "res_2")
        bad.side_effect = None
        bad.respond(400, json={"superseded_by": ["no such resource 'nope'"]})
        patch = route_superseded(mock, "res_3")
        admin.supersede_resources(supersede_args("res_1", "res_2", "res_3", by="nope"))
    assert "not in the registry" in caplog.text
    assert "no such resource 'nope'" in caplog.text
    assert patch.called
