# -*- mode: python -*-
"""Tests of finding and copying resources for transfer, against a live registry."""

import httpx
import pytest

from nbank import archive as nbank_archive
from nbank import core, transfer


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


def test_receive_and_add_location(cli, registry, make_archive, register, tmp_path):
    import io

    from nbank import util

    dest = make_archive(allow_directories=True)
    data = b"file contents"
    file_id = register(sha1=util.hash_stream(io.BytesIO(data)))["name"]
    src = tmp_path / "dir_resource"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "data").write_bytes(b"inside")
    dir_id = register(sha1=util.hash_directory(src))["name"]

    received = transfer.receive_file(
        dest.config,
        file_id,
        "original.wav",
        io.BytesIO(data),
        core.describe(registry.url, file_id)["sha1"],
    )
    assert received.verified
    with open(src / "sub" / "data", "rb") as fp:
        received_dir = transfer.receive_directory(
            dest.config,
            dir_id,
            dir_id,
            [("sub", None), ("sub/data", fp)],
            core.describe(registry.url, dir_id)["sha1"],
        )
    assert received_dir.verified
    with httpx.Client(auth=registry.auth) as session:
        for id, stored in ((file_id, received), (dir_id, received_dir)):
            transfer.add_location(session, registry.url, id, dest.name, stored.path)
    for id in (file_id, dir_id):
        assert core.describe(registry.url, id)["locations"] == [dest.name]
    assert cli("check", "archive", str(dest.path)) == 0


def test_add_location_refused(registry, archive, register, unique):
    import io

    name = register()["name"]
    received = transfer.receive_file(archive.config, name, "f", io.BytesIO(b"x"), None)
    with (
        httpx.Client(auth=registry.auth) as session,
        pytest.raises(transfer.TransferError, match="refused"),
    ):
        transfer.add_location(
            session, registry.url, name, unique("arch"), received.path
        )
    assert not received.path.exists()
