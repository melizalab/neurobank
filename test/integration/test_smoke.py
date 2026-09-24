# -*- mode: python -*-
import uuid

import httpx

from nbank import registry as reg
from nbank import util


def test_registry_info(registry):
    url, params = reg.get_info(registry.url)
    with httpx.Client() as session:
        info = util.query_registry(session, url, params)
    assert isinstance(info, dict)


def test_authenticated_write(registry):
    name = f"smoke-{uuid.uuid4().hex[:8]}"
    url, params = reg.add_datatype(registry.url, name, "text/plain")
    r = httpx.post(url, json=params, auth=registry.auth)
    assert r.status_code == 201
    assert r.json()["name"] == name
