# -*- mode: python -*-
import json
import stat

import httpx
import pytest
import respx

from nbank import archive, transfer, util
from test.test_registry import base_url


@pytest.fixture(autouse=True, scope="session")
def writable_tmp_dirs(tmp_path_factory):
    """Lets pytest remove old temporary directories that contain archives.

    Archives make directory resources read-only, and pytest can't remove their
    contents when it cleans up after later sessions.
    """
    yield
    base = tmp_path_factory.getbasetemp()
    for path in [base, *base.rglob("*")]:
        if path.is_dir() and not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IRWXU)


class _AllRegistries(set):
    def __contains__(self, item):
        return True


@pytest.fixture(autouse=True)
def registry_version_checked(monkeypatch):
    """Treats every registry's API version as checked, so tests mock only the
    requests they're about. Tests of the check replace it with an empty set."""
    monkeypatch.setattr(util, "_checked_registries", _AllRegistries())


@pytest.fixture
def tmp_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, base_url, umask=0o027, require_hash=False)


@pytest.fixture
def mocked_api():
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as respx_mock:
        yield respx_mock


@pytest.fixture
def registry_checks(mocked_api):
    """Returns a function that mocks the registry's check endpoint, accepting
    every resource, and returns the route."""

    def accept():
        route = mocked_api.post(f"{base_url}check/resources/")
        route.side_effect = lambda request: httpx.Response(
            200, json=[{} for _ in json.loads(request.content)]
        )
        return route

    return accept


@pytest.fixture
def failing_tar(tmp_path, monkeypatch):
    """Returns a function that writes members to a tar file that fails after
    limit bytes, read in 10240-byte blocks."""
    from test.test_transfer import FailingFile, tar_bytes

    monkeypatch.setattr(transfer, "_tape_read_size", 10240)

    def make(members, limit):
        path = tmp_path / "damaged.tar"
        path.write_bytes(tar_bytes(members))
        real_open = open

        def fake_open(p, *args, **kwargs):
            if p == path:
                return FailingFile(p, limit)
            return real_open(p, *args, **kwargs)

        monkeypatch.setattr(transfer, "open", fake_open, raising=False)
        return path

    return make
