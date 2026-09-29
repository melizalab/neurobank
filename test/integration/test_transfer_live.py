# -*- mode: python -*-
"""Tests of finding and copying resources for transfer, against a live registry."""

import httpx
import pytest

from nbank import archive as nbank_archive
from nbank import transfer


def find(registry, ids, **kwargs):
    with httpx.Client(auth=registry.auth) as session:
        return list(transfer.find_sources(session, registry.url, ids, **kwargs))


@pytest.fixture
def two_archives(archive, make_archive):
    return archive, make_archive(require_hash=False)


def test_find_sources(
    registry, two_archives, dtype, deposit_file, replicate, register, unique
):
    a, b = two_archives
    name = deposit_file(a, dtype)
    replicate(name, a, b)
    offsite = register(archive=None)["name"]
    missing = unique("missing")
    sources = find(registry, [name, offsite, missing])
    assert [s.id for s in sources] == [name, offsite, missing]
    assert sources[0].ok
    assert sources[0].archive in (a.name, b.name)
    assert sources[0].path == nbank_archive.resource_path(
        (a if sources[0].archive == a.name else b).config, name, resolve_ext=True
    )
    assert not sources[1].ok and not sources[2].ok

    [from_b] = find(registry, [name], archive=b.name)
    assert from_b.archive == b.name
    assert from_b.path == nbank_archive.resource_path(b.config, name, resolve_ext=True)
