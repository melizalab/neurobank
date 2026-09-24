# -*- mode: python -*-
import uuid

import httpx

from nbank import registry as reg
from nbank import util

# the major version of the registry API that the client is written for
API_MAJOR_VERSION = "1"


def test_registry_info(registry):
    url, params = reg.get_info(registry.url)
    with httpx.Client() as session:
        info = util.query_registry(session, url, params)
    assert isinstance(info, dict)


def test_api_version(registry):
    url, params = reg.get_info(registry.url)
    with httpx.Client() as session:
        info = util.query_registry(session, url, params)
    assert info["api_version"].split(".")[0] == API_MAJOR_VERSION


def test_authenticated_write(registry):
    name = f"smoke-{uuid.uuid4().hex[:8]}"
    url, params = reg.add_datatype(registry.url, name, "text/plain")
    r = httpx.post(url, json=params, auth=registry.auth)
    assert r.status_code == 201
    assert r.json()["name"] == name
