# -*- mode: python -*-
import json
import math
from types import SimpleNamespace

import pytest

from nbank import archive, core, readers
from test.test_nbank import archive_name, auth, auth_enc
from test.test_registry import archives_url, datatypes_url, resource_url

DTYPE = "fake-json"


class FakeEntryPoint:
    """Stands in for an importlib.metadata.EntryPoint"""

    def __init__(self, name, reader, package="fake-producer"):
        self.name = name
        self.value = f"{package.replace('-', '_')}:read_metadata"
        self.dist = SimpleNamespace(name=package)
        self._reader = reader

    def load(self):
        if isinstance(self._reader, Exception):
            raise self._reader
        return self._reader


def fake_reader(path):
    """A reader like the pprox one: a JSON object with a known $schema, whose
    "metadata" goes to the registry."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        raise ValueError(f"not JSON: {err}") from err
    if not isinstance(data, dict) or data.get("$schema") != "fake":
        raise ValueError("no fake $schema")
    return data.get("metadata", {})


@pytest.fixture
def install(monkeypatch):
    """Returns a function that makes the given entry points the installed readers"""

    def install(*eps):
        monkeypatch.setattr(
            readers,
            "entry_points",
            lambda group: [*eps] if group == readers.ENTRY_POINT_GROUP else [],
        )

    install()
    return install


@pytest.fixture
def make_file(tmp_path):
    def make(name, metadata=None, schema="fake"):
        path = tmp_path / name
        path.write_text(json.dumps({"$schema": schema, "metadata": metadata or {}}))
        return path

    return make


# discovery


def test_no_reader(install):
    assert readers.find_reader(DTYPE) is None


def test_reader_is_found_by_dtype(install):
    install(FakeEntryPoint("other", None), FakeEntryPoint(DTYPE, fake_reader))
    assert readers.find_reader(DTYPE) is fake_reader


def test_two_readers_for_a_dtype_is_an_error(install):
    install(
        FakeEntryPoint(DTYPE, fake_reader, "pkg-a"),
        FakeEntryPoint(DTYPE, fake_reader, "pkg-b"),
    )
    with pytest.raises(RuntimeError, match="pkg-a, pkg-b"):
        readers.find_reader(DTYPE)


@pytest.mark.parametrize("bad", [ImportError("no module arfx"), "not a function"])
def test_reader_that_cant_be_loaded_is_an_error(install, bad):
    install(FakeEntryPoint(DTYPE, bad))
    with pytest.raises(RuntimeError, match="fake-producer"):
        readers.find_reader(DTYPE)


# what readers return


def read(reader, path="file"):
    from pathlib import Path

    return readers.read_file_metadata(reader, DTYPE, Path(path))


def test_reader_rejecting_a_file():
    def reader(path):
        raise ValueError("no entries")

    with pytest.raises(ValueError, match=f"not a valid {DTYPE} file: no entries"):
        read(reader)


def test_reader_failing_is_reported_with_its_type():
    def reader(path):
        raise KeyError("timestamp")

    with pytest.raises(ValueError, match="reader for fake-json failed: KeyError"):
        read(reader)


@pytest.mark.parametrize(
    "returned, message",
    [
        (["experimenter"], "returned list, not a dict"),
        ({1: "x"}, "key that isn't a string"),
        # h5py gives attributes as numpy scalars and arrays, which aren't JSON;
        # this stands in for one (numpy isn't installed here)
        ({"pen": SimpleNamespace(item=lambda: 1)}, "value for 'pen'"),
        ({"channels": {1, 2}}, "value for 'channels'"),
        # HDF5 attributes can hold NaN, which JSON (and Postgres) can't
        ({"depth": math.nan}, "value for 'depth'"),
        ({"bytes": b"P50"}, "value for 'bytes'"),
    ],
)
def test_reader_returning_what_cant_be_stored(returned, message):
    with pytest.raises(ValueError, match=message):
        read(lambda path: returned)


def test_keys_without_values_are_left_out():
    assert read(lambda path: {"pen": 1, "site": None}) == {"pen": 1}


# merging


def test_merge_keeps_keys_from_both():
    assert readers.merge_metadata({"pen": 1}, {"site": 2}) == {"pen": 1, "site": 2}


def test_merge_accepts_matching_values():
    # an integer and a float with the same value agree
    assert readers.merge_metadata({"pen": 1.0}, {"pen": 1}) == {"pen": 1}


@pytest.mark.parametrize(
    "from_file, given",
    [
        ({"animal": "P50"}, {"animal": "P51"}),
        # -k pen=1 is read as a number; a reader returning a string disagrees
        ({"pen": "1"}, {"pen": 1}),
        # True == 1 in Python, but they're different values
        ({"flag": True}, {"flag": 1}),
    ],
)
def test_merge_refuses_different_values(from_file, given):
    key = next(iter(given))
    with pytest.raises(ValueError, match=f"{key}: .* in the file, .* given"):
        readers.merge_metadata(from_file, given)


def test_merge_override_uses_given_values():
    merged = readers.merge_metadata(
        {"animal": "P50", "pen": 1}, {"animal": "P51"}, override=True
    )
    assert merged == {"animal": "P51", "pen": 1}


# deposit


@pytest.fixture
def archive_routes(mocked_api, tmp_archive):
    root = tmp_archive["path"]
    mocked_api.get(
        archives_url, params={"scheme": "neurobank", "root": str(root)}
    ).respond(json=[{"name": archive_name, "root": str(root)}])
    mocked_api.get(datatypes_url).respond(
        json=[{"name": DTYPE, "content_type": "application/json"}]
    )
    return root


def deposit(root, *files, **kwargs):
    return list(
        core.deposit(
            root, files=files, dtype=DTYPE, auth=auth, skip_errors=True, **kwargs
        )
    )


def test_deposit_registers_file_metadata(
    install, mocked_api, archive_routes, make_file
):
    install(FakeEntryPoint(DTYPE, fake_reader))
    src = make_file("res_1", {"animal": "P50", "pen": 1})
    mocked_api.post(
        resource_url,
        json={
            "name": "res_1",
            "dtype": DTYPE,
            "locations": [archive_name],
            # the file's animal is a field, like a given one
            "animal": "P50",
            "metadata": {"pen": 1, "site": 2},
        },
        headers={"Authorization": f"Basic {auth_enc}"},
    ).respond(json={"name": "res_1"})
    items = deposit(archive_routes, src, metadata={"site": 2})
    assert items == [{"source": src, "id": "res_1"}]


@pytest.mark.parametrize(
    "contents, given, message",
    [
        ({"$schema": "other"}, {}, "not a valid fake-json file: no fake $schema"),
        ({"$schema": "fake", "metadata": {"pen": 1}}, {"pen": 2}, "pen: 1 in the file"),
    ],
)
def test_deposit_skips_files_that_fail(
    install, archive_routes, tmp_path, contents, given, message
):
    install(FakeEntryPoint(DTYPE, fake_reader))
    src = tmp_path / "res_1"
    src.write_text(json.dumps(contents))
    # no resource POST is mocked: nothing must be registered
    [item] = deposit(archive_routes, src, metadata=given)
    assert message in item["error"]
    assert src.exists()


def test_deposit_raises_without_skip_errors(install, archive_routes, make_file):
    install(FakeEntryPoint(DTYPE, fake_reader))
    src = make_file("res_1", schema="other")
    with pytest.raises(ValueError, match="not a valid"):
        list(core.deposit(archive_routes, files=[src], dtype=DTYPE, auth=auth))


def test_deposit_override(install, mocked_api, archive_routes, make_file):
    install(FakeEntryPoint(DTYPE, fake_reader))
    src = make_file("res_1", {"pen": 1})
    mocked_api.post(resource_url, json__metadata={"pen": 2}).respond(
        json={"name": "res_1"}
    )
    items = deposit(
        archive_routes, src, metadata={"pen": 2}, override_file_metadata=True
    )
    assert items == [{"source": src, "id": "res_1"}]


def test_deposit_dry_run_shows_merged_metadata(install, archive_routes, make_file):
    install(FakeEntryPoint(DTYPE, fake_reader))
    src = make_file("res_1", {"pen": 1})
    [item] = deposit(archive_routes, src, metadata={"site": 2}, dry_run=True)
    assert item["metadata"] == {"pen": 1, "site": 2}
    assert not archive.resource_path(
        archive.get_config(archive_routes), "res_1"
    ).exists()


def test_deposit_without_reading_metadata(install, archive_routes, make_file):
    def reader(path):
        raise AssertionError("the reader must not be called")

    install(FakeEntryPoint(DTYPE, reader))
    src = make_file("res_1", schema="other")
    [item] = deposit(
        archive_routes, src, metadata={"site": 2}, dry_run=True, read_metadata=False
    )
    assert item["metadata"] == {"site": 2}


def test_deposit_without_a_reader(install, archive_routes, make_file):
    src = make_file("res_1", {"pen": 1})
    [item] = deposit(archive_routes, src, dry_run=True)
    assert item["metadata"] == {}


def test_deposit_reader_is_loaded_once(install, archive_routes, make_file):
    loads = []

    class Counting(FakeEntryPoint):
        def load(self):
            loads.append(1)
            return super().load()

    install(Counting(DTYPE, fake_reader))
    files = [make_file(f"res_{i}") for i in range(3)]
    deposit(archive_routes, *files, dry_run=True)
    assert len(loads) == 1


def test_deposit_with_a_broken_reader_fails_before_any_file(install, archive_routes):
    install(FakeEntryPoint(DTYPE, ImportError("no module named h5py")))
    with pytest.raises(RuntimeError, match="can't load the metadata reader"):
        deposit(archive_routes)
