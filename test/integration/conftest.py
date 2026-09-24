# -*- mode: python -*-
"""Fixtures for running tests against a live registry.

By default a registry server is started in a subprocess with a fresh database.
This needs the `integration` dependency group:

    uv run --group integration pytest -m integration

To use an existing registry instead, set NBANK_TEST_REGISTRY to its base URL
and NBANK_TEST_AUTH to "user:password" for an account that can write.
"""

import importlib.util
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import httpx
import pytest

from nbank import archive as nbank_archive
from nbank import core, script
from nbank import registry as reg

ROOT = Path(__file__).parents[2]
SETTINGS_MODULE = "test.integration.server.settings"
USERNAME = "nbank-test"
PASSWORD = "nbank-test-pw"
STARTUP_TIMEOUT = 30


class Registry(NamedTuple):
    url: str
    auth: tuple[str, str]


class Archive(NamedTuple):
    name: str
    path: Path
    config: dict


def pytest_collection_modifyitems(items):
    """Mark every test in this directory as an integration test."""
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def manage(env: dict, *args: str) -> None:
    """Run a Django management command against the test settings."""
    result = subprocess.run(
        [sys.executable, "-m", "django", *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"'manage.py {args[0]}' failed:\n{result.stdout}{result.stderr}")


def wait_for_server(url: str, proc: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"registry server exited early:\n{log.read_text()}")
        try:
            if httpx.get(url + "info/").status_code == 200:
                return
        except httpx.TransportError:
            time.sleep(0.2)
    pytest.fail(f"registry server did not start:\n{log.read_text()}")


@pytest.fixture(scope="session")
def registry(tmp_path_factory):
    """Base URL and credentials of a registry that tests can write to."""
    external = os.environ.get("NBANK_TEST_REGISTRY")
    if external:
        user, _, password = os.environ["NBANK_TEST_AUTH"].partition(":")
        yield Registry(external, (user, password))
        return

    if importlib.util.find_spec("nbank_registry") is None:
        pytest.fail(
            "django-neurobank is not installed. "
            "Run: uv run --group integration pytest -m integration"
        )
    tmp = tmp_path_factory.mktemp("registry")
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": SETTINGS_MODULE,
        "PYTHONPATH": str(ROOT),
        "NBANK_TEST_SQLITE": str(tmp / "registry.sqlite3"),
    }
    manage(env, "migrate", "--noinput", "-v", "0")
    manage(
        env,
        "shell",
        "-c",
        "from django.contrib.auth.models import User;"
        f"User.objects.filter(username='{USERNAME}').delete();"
        f"User.objects.create_superuser('{USERNAME}', password='{PASSWORD}')",
    )
    port = free_port()
    addr = f"127.0.0.1:{port}"
    url = f"http://{addr}/neurobank/"
    log = tmp / "server.log"
    with open(log, "w") as fp:
        proc = subprocess.Popen(
            [sys.executable, "-m", "django", "runserver", "--noreload", addr],
            env=env,
            cwd=ROOT,
            stdout=fp,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_server(url, proc, log)
            yield Registry(url, (USERNAME, PASSWORD))
        finally:
            proc.terminate()
            proc.wait(timeout=10)


@pytest.fixture
def unique():
    """Returns a function that makes names that are unique within the registry."""

    def make(prefix: str = "t") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    return make


@pytest.fixture
def client(registry):
    """An HTTP client that is authenticated to the registry."""
    with httpx.Client(auth=registry.auth) as session:
        yield session


@pytest.fixture
def dtype(client, registry, unique):
    """The name of a new datatype."""
    name = unique("dtype")
    url, body = reg.add_datatype(registry.url, name, "application/octet-stream")
    client.post(url, json=body).raise_for_status()
    return name


@pytest.fixture
def make_archive(client, registry, unique, tmp_path):
    """Returns a function that creates an archive and registers it.

    Keyword arguments set archive policies (e.g., require_hash).
    """

    def make(**policies) -> Archive:
        name = unique("arch")
        config = nbank_archive.create(tmp_path / name, registry.url, **policies)
        url, body = reg.add_archive(registry.url, name, "neurobank", config["path"])
        client.post(url, json=body).raise_for_status()
        return Archive(name, config["path"], config)

    return make


@pytest.fixture
def archive(make_archive):
    """A registered archive that does not require hashes."""
    return make_archive(require_hash=False)


@pytest.fixture
def register(client, registry, unique, dtype):
    """Returns a function that adds a resource record directly, without a file.

    Returns the record from the registry.
    """

    def make(name=None, *, dtype=dtype, archive=None, sha1=None, **metadata) -> dict:
        name = name or unique("res")
        url, body = reg.add_resource(
            registry.url, name, dtype, archive, sha1, **metadata
        )
        if archive is None:
            body["locations"] = []
        r = client.post(url, json=body)
        r.raise_for_status()
        return r.json()

    return make


@pytest.fixture
def make_netrc(registry):
    """Returns a function that writes a netrc file with the registry credentials."""

    def make(path: Path, password: str | None = None) -> Path:
        host = urlparse(registry.url).hostname
        user, correct = registry.auth
        path.write_text(
            f"machine {host}\nlogin {user}\npassword {password or correct}\n"
        )
        # the default netrc file is ignored if others can read it
        path.chmod(0o600)
        return path

    return make


@pytest.fixture
def netrc_home(make_netrc, tmp_path, monkeypatch):
    """A home directory with a .netrc that has the registry credentials."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    make_netrc(home / ".netrc")
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def cli(registry, netrc_home):
    """Returns a function that runs the nbank command line against the registry.

    Arguments are what follows the global `-r` option (e.g., `-a user:pw`, then
    the subcommand). The command line parser needs ~/.netrc to exist, so the
    home directory has one with the registry credentials, which also serves as
    the default login. The logger is restored afterward because each run adds a
    handler to it.
    """
    log = logging.getLogger("nbank")
    handlers, level = list(log.handlers), log.level

    def run(*args: str):
        return script.main(["-r", registry.url, *args])

    yield run
    log.handlers[:] = handlers
    log.setLevel(level)


@pytest.fixture
def deposit_file(registry, tmp_path, unique):
    """Returns a function that makes a file and deposits it. Returns the id."""

    def deposit(archive: Archive, dtype: str, contents: str | None = None, **kwargs):
        name = unique("res")
        src = tmp_path / f"{name}.txt"
        src.write_text(contents or name)
        [item] = core.deposit(
            archive.path, [src], dtype=dtype, auth=registry.auth, **kwargs
        )
        return item["id"]

    return deposit


@pytest.fixture
def replicate(registry, client, tmp_path):
    """Returns a function that copies a resource to another archive."""

    def copy(name: str, src: Archive, dst: Archive) -> None:
        stored = nbank_archive.resource_path(src.config, name, resolve_ext=True)
        tmp = tmp_path / f"copy-{stored.name}"
        shutil.copy(stored, tmp)
        nbank_archive.store_resource(dst.config, tmp, id=name)
        url, body = reg.add_location(registry.url, name, dst.name)
        client.post(url, json=body).raise_for_status()

    return copy
