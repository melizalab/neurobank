# -*- mode: python -*-
"""Tests of the nbank command line against a live registry."""

import io
import json

import pytest

from nbank import archive as nbank_archive
from nbank import core, util
from nbank import registry as reg


def json_objects(text):
    """Parses concatenated JSON objects, as printed by several commands."""
    decoder = json.JSONDecoder()
    text = text.strip()
    objects, pos = [], 0
    while pos < len(text):
        obj, pos = decoder.raw_decode(text, pos)
        objects.append(obj)
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return objects


def test_registry_info(cli, registry, caplog):
    cli("registry-info")
    assert f"address: {registry.url}" in caplog.text
    assert "name: django-neurobank" in caplog.text


def test_init(cli, registry, client, tmp_path, unique):
    path = tmp_path / unique("arch")
    cli("init", str(path))
    url, _ = reg.get_archive(registry.url, path.name)
    record = client.get(url).json()
    assert record["scheme"] == "neurobank"
    assert record["root"] == str(path.resolve())
    assert nbank_archive.get_config(path)["registry"] == registry.url


def test_init_with_name(cli, registry, client, tmp_path, unique):
    name = unique("arch")
    cli("init", "-n", name, str(tmp_path / "archive"))
    url, _ = reg.get_archive(registry.url, name)
    assert client.get(url).status_code == 200


def test_init_duplicate_name(cli, archive, tmp_path, caplog):
    path = tmp_path / "second"
    cli("init", "-n", archive.name, str(path))
    assert "an archive with this name already exists" in caplog.text
    assert not path.exists()


def test_deposit(cli, registry, archive, dtype, tmp_path, unique, capsys):
    names = [unique("res"), unique("res")]
    files = []
    for name in names:
        files.append(tmp_path / f"{name}.txt")
        files[-1].write_text(name)
    hashes = [util.hash(f) for f in files]
    cli(
        *["deposit", "-d", dtype, "-H", "-j"],
        *["-k", "experimenter=dmeliza", "-k", "n=3"],
        str(archive.path),
        *map(str, files),
    )
    items = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["id"] for item in items] == names
    assert [item["source"] for item in items] == [str(f) for f in files]
    for name, sha1 in zip(names, hashes, strict=True):
        record = core.describe(registry.url, name)
        assert record["sha1"] == sha1
        assert record["metadata"] == {"experimenter": "dmeliza", "n": 3}


@pytest.mark.xfail(strict=True, reason="names read from stdin are str, not Path")
def test_deposit_names_from_stdin(
    cli, registry, archive, dtype, tmp_path, unique, monkeypatch
):
    names = [unique("res"), unique("res")]
    files = [tmp_path / f"{name}.txt" for name in names]
    for f in files:
        f.write_text("contents-" + f.stem)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{files[1]}\n"))
    cli("deposit", "-d", dtype, "-@", str(archive.path), str(files[0]))
    found = core.describe_many(registry.url, *names)
    assert {r["name"] for r in found} == set(names)


def test_deposit_invalid_name(cli, registry, archive, dtype, tmp_path, caplog):
    src = tmp_path / "not valid.txt"
    src.write_text("contents")
    cli("deposit", "-d", dtype, str(archive.path), str(src))
    assert "error:" in caplog.text
    assert src.exists()


def test_deposit_name_rejected_by_registry(
    cli, registry, archive, dtype, tmp_path, unique, caplog
):
    # the client accepts '~' in ids, but the registry does not
    name = unique("res") + "~x"
    src = tmp_path / f"{name}.txt"
    src.write_text("contents")
    cli("deposit", "-d", dtype, str(archive.path), str(src))
    assert "can only contain letters, numbers, underscores" in caplog.text
    assert src.exists()
    assert core.describe(registry.url, name) is None


def test_info(cli, register, unique, capsys):
    name, missing = register()["name"], unique("missing")
    cli("info", name, missing)
    found, not_found = json_objects(capsys.readouterr().out)
    assert found["name"] == name
    assert not_found == {"id": missing, "error": "not found"}


def test_search_by_metadata(cli, register, dtype, unique, capsys):
    tag = unique("grp")
    a = register(group=tag, n=3)["name"]
    b = register(group=tag, n=4)["name"]
    c = register(group="other")["name"]

    def search(*args):
        cli("search", "-d", dtype, *args)
        return set(capsys.readouterr().out.split())

    assert search("-k", f"group={tag}") == {a, b}
    assert search("-k", f"group={tag}", "-k", "n=3") == {a}
    assert search("-K", f"group={tag}") == {c}


def test_search_by_hash_archive_and_name(cli, register, archive, unique, capsys):
    tag = unique("tag")
    sha1 = "0123456789abcdef0123456789abcdef" + tag[-8:]
    a = register(f"{tag}-a", archive=archive.name, sha1=sha1)["name"]
    b = register(f"{tag}-b")["name"]

    def search(*args):
        cli("search", *args)
        return set(capsys.readouterr().out.split())

    assert search("-H", sha1) == {a}
    assert search("-n", archive.name) == {a}
    assert search(tag) == {a, b}


def test_search_json(cli, register, dtype, capsys):
    name = register(k="v")["name"]
    cli("search", "-j", "-d", dtype)
    [record] = json_objects(capsys.readouterr().out)
    assert record["name"] == name
    assert record["metadata"] == {"k": "v"}


def test_search_requires_a_filter(cli, caplog):
    cli("search")
    assert "at least one filter parameter is required" in caplog.text


def test_locate(cli, registry, archive, dtype, deposit_file, capsys, unique):
    name = deposit_file(archive, dtype)
    path = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    cli("locate", name)
    assert capsys.readouterr().out == f"{name:<20}\t{path}\n"
    # a full resource URL is also accepted
    cli("locate", reg.full_url(registry.url, name))
    assert capsys.readouterr().out == f"{name:<20}\t{path}\n"
    missing = unique("missing")
    cli("locate", missing)
    assert "(not found)" in capsys.readouterr().out


def test_locate_null_separated(cli, archive, dtype, deposit_file, capsys):
    name = deposit_file(archive, dtype)
    path = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    cli("locate", "-0", name)
    assert capsys.readouterr().out == f"{path}\0"


def test_locate_link(cli, archive, dtype, deposit_file, tmp_path):
    name = deposit_file(archive, dtype)
    path = nbank_archive.resource_path(archive.config, name, resolve_ext=True)
    links = tmp_path / "links"
    links.mkdir()
    cli("locate", "-L", str(links), name)
    assert (links / path.name).resolve() == path


def test_modify(cli, registry, register, capsys):
    name = register(a=1, b=2)["name"]
    cli("modify", "-k", "b=3", "-k", "c=new", "-K", "a", name)
    [result] = json_objects(capsys.readouterr().out)
    assert result["metadata"] == {"b": 3, "c": "new"}
    assert core.describe(registry.url, name)["metadata"] == result["metadata"]


def test_verify_matching_id(cli, register, tmp_path, unique, capsys):
    name = unique("res")
    src = tmp_path / f"{name}.txt"
    src.write_text(unique("contents"))
    register(name, sha1=util.hash(src))
    cli("verify", str(src))
    assert capsys.readouterr().out == f"{src}: OK\n"


def test_verify_changed_contents(cli, register, tmp_path, unique, capsys):
    name = unique("res")
    src = tmp_path / f"{name}.txt"
    src.write_text("original")
    register(name, sha1=util.hash(src))
    src.write_text("changed")
    cli("verify", str(src))
    assert capsys.readouterr().out == f"{src}: FAILED to match record for {name}\n"


def test_verify_finds_other_name(cli, register, tmp_path, unique, capsys):
    src = tmp_path / f"{unique('res')}.txt"
    src.write_text(unique("contents"))
    other = register(sha1=util.hash(src))["name"]
    cli("verify", str(src))
    assert capsys.readouterr().out == f"{src}: matches registry resource {other}\n"


def test_verify_no_match(cli, tmp_path, unique, capsys):
    src = tmp_path / f"{unique('res')}.txt"
    src.write_text(unique("contents"))
    cli("verify", str(src))
    assert capsys.readouterr().out == f"{src}: no matches in registry\n"


def test_verify_missing_file(cli, tmp_path, capsys):
    src = tmp_path / "missing.txt"
    cli("verify", str(src))
    assert capsys.readouterr().out == f"{src}: no such file or directory\n"


def test_dtype_add_and_list(cli, unique, capsys, caplog):
    name = unique("dtype")
    cli("dtype", "add", name, "text/plain")
    cli("dtype", "list")
    assert f"{name:<25}\t(text/plain)" in capsys.readouterr().out
    caplog.clear()
    cli("dtype", "add", name, "text/plain")
    assert "a dtype with this name already exists" in caplog.text


def test_archive_list(cli, archive, capsys):
    cli("archive", "list", "-n", archive.name)
    assert capsys.readouterr().out == f"{archive.name:<25}\t{archive.path}\n"
    cli("archive", "list", "-n", archive.name, "--scheme", "tape")
    assert capsys.readouterr().out == ""
