# -*- coding: utf-8 -*-
# -*- mode: python -*-
import pytest
from nbank import registry

base_url = "https://localhost:8000/neurobank/"
id = "adfkj"
full = "https://localhost:8000/neurobank/resources/adfkj/"

resource_url = "https://localhost:8000/neurobank/resources/"
info_url = "https://localhost:8000/neurobank/info/"
datatypes_url = "https://localhost:8000/neurobank/datatypes/"
archives_url = "https://localhost:8000/neurobank/archives/"
download_url = "https://localhost:8000/neurobank/download/"
bulk_url = "https://localhost:8000/neurobank/bulk/"


def test_join_url():
    assert registry.url_join(base_url, "resources/") == resource_url


def test_short_to_full():
    assert registry.full_url(base_url, id) == full


def test_short_to_full_noslash():
    assert registry.full_url(base_url.rstrip("/"), id) == full


def test_full_to_parts():
    BB, II = registry.parse_resource_url(full)
    assert BB == base_url
    assert II == id


def test_full_to_parts_noslash():
    BB, II = registry.parse_resource_url(full.rstrip("/"))
    assert BB == base_url
    assert II == id


def test_parts_to_parts():
    url = registry.full_url(base_url, id)
    BB, II = registry.parse_resource_url(url)
    assert BB == base_url
    assert II == id


def test_incomplete_resource_url():
    with pytest.raises(ValueError):
        registry.parse_resource_url(id)


def test_bad_resource_url_characters():
    url = registry.full_url(base_url, "j@*")
    with pytest.raises(ValueError):
        registry.parse_resource_url(url)


def test_get_info():
    url, params = registry.get_info(base_url)
    assert url == registry.url_join(base_url, "info/")
    assert params is None


def test_get_datatypes():
    url, params = registry.get_datatypes(base_url)
    assert url == registry.url_join(base_url, "datatypes/")
    assert params is None


def test_get_archives():
    url, params = registry.get_archives(base_url)
    assert url == registry.url_join(base_url, "archives/")
    assert params is None


def test_find_archive():
    test_path = "/test/path"
    url, params = registry.find_archive_by_path(base_url, test_path)
    assert url == registry.url_join(base_url, "archives/")
    assert params == {"scheme": registry._neurobank_scheme, "root": test_path}


def test_find_resource():
    test_params = {"experimenter": "dmeliza", "sha1": "1234"}
    url, params = registry.find_resource(base_url, **test_params)
    assert url == resource_url
    assert params == test_params


def test_get_resource():
    url, params = registry.get_resource(base_url, id)
    assert url == full
    assert params is None


def test_get_resource_bulk():
    ids = (id, "abcd3")
    url, params = registry.get_resource_bulk(base_url, *ids)
    assert url == registry.url_join(bulk_url, "resources/")
    assert params == {"names": ids}


def test_fetch_resource():
    url, params = registry.fetch_resource(base_url, id)
    assert url == registry.url_join(download_url, id)
    assert params is None


def test_get_locations():
    url, params = registry.get_locations(base_url, id)
    assert url == registry.url_join(full, "locations/")
    assert params == {}


def test_get_locations_bulk():
    ids = (id, "abcd3")
    url, params = registry.get_locations_bulk(base_url, *ids)
    assert url == registry.url_join(bulk_url, "locations/")
    assert params == {"names": ids}


def test_get_locations_with_params():
    test_params = {"archive": "registry"}
    url, params = registry.get_locations(base_url, id, **test_params)
    assert url == registry.url_join(full, "locations/")
    assert params == test_params


def test_add_datatype():
    test_name = "my-dtype"
    test_content_type = "audio/wav"
    url, params = registry.add_datatype(base_url, test_name, test_content_type)
    assert url == datatypes_url
    assert params == {"name": test_name, "content_type": test_content_type}


def test_add_archive():
    test_name = "my-archive"
    test_scheme = "dummy"
    test_root = "/a/dummy/path"
    url, params = registry.add_archive(base_url, test_name, test_scheme, test_root)
    assert url == archives_url
    assert params == {"name": test_name, "scheme": test_scheme, "root": test_root}


def test_add_resource():
    test_id = "my-resource"
    test_dtype = "my-dtype"
    test_archive = "my-archive"
    experimenter = "dmeliza"
    url, params = registry.add_resource(
        base_url, test_id, test_dtype, test_archive, experimenter=experimenter
    )
    assert url == resource_url
    assert params == {
        "name": test_id,
        "dtype": test_dtype,
        "locations": [test_archive],
        "metadata": {"experimenter": experimenter},
    }


def test_update_metadata():
    experimenter = "dmeliza"
    url, params = registry.update_resource_metadata(
        base_url, id, experimenter=experimenter
    )
    assert url == full
    assert params == {
        "metadata": {"experimenter": experimenter},
    }


# def test_id_with_slashes_ok():
#     locations = registry.get_locations(base_url, "a/relative/path/a/user/might/look/up")
#     .assertEqual(locations, [])

# def test_id_with_initial_slash_ok():
#     locations = registry.get_locations(
#         base_url, "/an/absolute/path/a/user/might/look/up"
#     )
#     .assertEqual(locations, [])
