# -*- mode: python -*-
import stat

import pytest


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
