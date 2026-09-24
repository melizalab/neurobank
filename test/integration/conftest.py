# -*- mode: python -*-
"""Fixtures for running tests against a live registry.

By default a registry server is started in a subprocess with a fresh database.
This needs the `integration` dependency group:

    uv run --group integration pytest -m integration

To use an existing registry instead, set NBANK_TEST_REGISTRY to its base URL
and NBANK_TEST_AUTH to "user:password" for an account that can write.
"""

import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest

ROOT = Path(__file__).parents[2]
SETTINGS_MODULE = "test.integration.server.settings"
USERNAME = "nbank-test"
PASSWORD = "nbank-test-pw"
STARTUP_TIMEOUT = 30


class Registry(NamedTuple):
    url: str
    auth: tuple[str, str]


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
