# -*- mode: python -*-
import os

# Windows has no uids
running_as_root = hasattr(os, "getuid") and os.getuid() == 0


def skip_unless_posix():
    """Skips the calling test on Windows, where archives can't be created."""
    import pytest

    from nbank import archive

    if not archive._posix:
        pytest.skip("archives are created on a POSIX host")


def with_access(cfg, **changes):
    """Returns an archive configuration with its access policy changed."""
    from dataclasses import replace

    access = replace(cfg.policy.access, **changes)
    return replace(cfg, policy=replace(cfg.policy, access=access))


def place_resource(cfg, id, key, contents="contents"):
    """Writes a file straight into an archive, where store_resource would put it,
    and returns its path."""
    from nbank import archive

    path = archive.resource_path(cfg, id).with_name(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


def mock_archive_locations(
    mocked_api, archive_name, root, resources, scheme="neurobank"
):
    """Mocks the registry's locations/ stream for an archive. resources is
    {name: (sha1, key)}; each resource's filename is name.txt, and its dtype txt.
    Returns the route."""
    import json

    from nbank import util
    from test.test_registry import locations_url

    records = [
        {
            "archive_name": archive_name,
            "scheme": scheme,
            "root": str(root),
            "resource_name": name,
            "key": key,
            "filename": f"{name}.txt",
            "sha1": sha1,
            "dtype": "txt",
        }
        for name, (sha1, key) in resources.items()
    ]
    return mocked_api.get(locations_url, params={"archive": archive_name}).respond(
        headers={"Content-Type": util.NDJSON},
        text="".join(json.dumps(r) + "\n" for r in records),
    )
