# -*- mode: python -*-
import stat

import pytest

from nbank import transfer


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
