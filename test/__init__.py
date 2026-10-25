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
