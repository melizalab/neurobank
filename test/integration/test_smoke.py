# -*- mode: python -*-
import uuid

from nbank import registry as reg
from nbank import util


def test_registry_info(registry):
    url, params = reg.get_info(registry.url)
    with util.registry_session() as session:
        info = util.query_registry(session, url, params)
    assert isinstance(info, dict)


def test_api_version(registry):
    url, params = reg.get_info(registry.url)
    with util.registry_session() as session:
        info = util.query_registry(session, url, params)
    assert info["api_version"].split(".")[0] == util.API_VERSION.split(".")[0]


def test_authenticated_write(registry):
    name = f"smoke-{uuid.uuid4().hex[:8]}"
    url, params = reg.add_datatype(registry.url, name, "text/plain")
    with util.registry_session(registry.url, auth=registry.auth) as session:
        r = session.post(url, json=params)
    assert r.status_code == 201
    assert r.json()["name"] == name
