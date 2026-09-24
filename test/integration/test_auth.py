# -*- mode: python -*-
"""Tests of how the client authenticates to the registry and reports failures."""

import logging

import httpx
import pytest

from nbank import core
from nbank import registry as reg


@pytest.fixture
def empty_home(tmp_path, monkeypatch):
    """A home directory with no .netrc."""
    home = tmp_path / "empty_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def bad_auth(registry):
    return (registry.auth[0], "wrong-password")


def update(registry, name, auth):
    return list(core.update(registry.url, name, auth=auth, k="v"))


def forbidden(registry, name, auth):
    """Returns the error raised by an update that the registry refuses."""
    with pytest.raises(httpx.HTTPStatusError) as err:
        update(registry, name, auth)
    assert err.value.response.status_code == 403
    return err.value


def test_username_password(registry, register):
    name = register()["name"]
    assert update(registry, name, registry.auth)[0]["metadata"] == {"k": "v"}


def test_auth_object(registry, register):
    name = register()["name"]
    auth = httpx.BasicAuth(*registry.auth)
    assert update(registry, name, auth)[0]["metadata"] == {"k": "v"}


def test_netrc_file(registry, register, make_netrc, tmp_path):
    name = register()["name"]
    auth = httpx.NetRCAuth(make_netrc(tmp_path / "netrc"))
    assert update(registry, name, auth)[0]["metadata"] == {"k": "v"}


def test_default_netrc(registry, register, netrc_home):
    name = register()["name"]
    assert update(registry, name, None)[0]["metadata"] == {"k": "v"}


def test_no_credentials(registry, register, empty_home):
    err = forbidden(registry, register()["name"], None)
    assert "not provided" in err.response.json()["detail"]


def test_wrong_password(registry, register, bad_auth):
    err = forbidden(registry, register()["name"], bad_auth)
    assert err.response.json()["detail"] == "Invalid username/password."


def test_deposit_refused(registry, archive, dtype, bad_auth, tmp_path, unique):
    name = unique("res")
    src = tmp_path / f"{name}.txt"
    src.write_text("contents")
    with pytest.raises(httpx.HTTPStatusError) as err:
        list(core.deposit(archive.path, [src], dtype=dtype, auth=bad_auth))
    assert err.value.response.status_code == 403
    assert src.exists()
    assert core.describe(registry.url, name) is None


def test_log_error_forbidden(registry, register, bad_auth, caplog):
    err = forbidden(registry, register()["name"], bad_auth)
    with caplog.at_level(logging.ERROR, logger="nbank"):
        reg.log_error(err)
    assert [r.getMessage() for r in caplog.records] == [
        "   registry error: Invalid username/password."
    ]


def test_log_error_validation(registry, client, dtype, caplog):
    url, body = reg.add_datatype(registry.url, dtype, "text/plain")
    with pytest.raises(httpx.HTTPStatusError) as err:
        client.post(url, json=body).raise_for_status()
    assert err.value.response.status_code == 400
    with caplog.at_level(logging.ERROR, logger="nbank"):
        reg.log_error(err.value)
    assert [r.getMessage() for r in caplog.records] == [
        "   registry error: name: a dtype with this name already exists"
    ]


@pytest.mark.xfail(strict=True, reason="log_error logs each character of a string")
def test_log_error_detail(registry, client, caplog):
    url, body = reg.get_resource_bulk(registry.url, [])
    with pytest.raises(httpx.HTTPStatusError) as err:
        client.post(url, json=body).raise_for_status()
    assert err.value.response.status_code == 400
    with caplog.at_level(logging.ERROR, logger="nbank"):
        reg.log_error(err.value)
    assert [r.getMessage() for r in caplog.records] == [
        "   registry error: detail: must supply at least one name"
    ]


@pytest.mark.xfail(strict=True, reason="errors from streamed requests are unread")
def test_log_error_streamed(registry, caplog):
    with pytest.raises(httpx.HTTPStatusError) as err:
        list(core.describe_many(registry.url))
    with caplog.at_level(logging.ERROR, logger="nbank"):
        reg.log_error(err.value)
    assert "must supply at least one name" in caplog.text


def test_cli_credentials_option(cli, registry, client, unique):
    name = unique("dtype")
    user, password = registry.auth
    cli("-a", f"{user}:{password}", "dtype", "add", name, "text/plain")
    url = reg.url_join(registry.url, "datatypes", f"{name}/")
    assert client.get(url).status_code == 200


def test_cli_default_netrc(cli, registry, client, unique):
    name = unique("dtype")
    cli("dtype", "add", name, "text/plain")
    url = reg.url_join(registry.url, "datatypes", f"{name}/")
    assert client.get(url).status_code == 200


def test_cli_credentials_override_netrc(cli, registry, client, unique, caplog):
    name = unique("dtype")
    user, _ = registry.auth
    cli("-a", f"{user}:wrong-password", "dtype", "add", name, "text/plain")
    assert "authentication error" in caplog.text
    url = reg.url_join(registry.url, "datatypes", f"{name}/")
    assert client.get(url).status_code == 404


@pytest.mark.xfail(strict=True, reason="the parser requires ~/.netrc to exist")
def test_cli_credentials_without_netrc(cli, registry, client, unique, empty_home):
    name = unique("dtype")
    user, password = registry.auth
    cli("-a", f"{user}:{password}", "dtype", "add", name, "text/plain")
    url = reg.url_join(registry.url, "datatypes", f"{name}/")
    assert client.get(url).status_code == 200
