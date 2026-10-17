# -*- mode: python -*-
import json
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
