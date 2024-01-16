# -*- mode: python -*-
import pytest

from nbank import archive

dummy_registry = "https://localhost:8000/neurobank"


@pytest.fixture()
def tmp_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry)


@pytest.fixture()
def tmp_dir_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry, allow_directories=True)


@pytest.fixture()
def tmp_noext_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, dummy_registry, keep_extensions=False)


def test_invalid_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        _ = archive.get_config(tmp_path)


def test_can_read_config(tmp_archive):
    cfg = archive.get_config(tmp_archive["path"])
    assert tmp_archive == cfg


def test_archive_umask(tmp_archive):
    # cfgtmpl = json.loads(archive._nbank_json)
    root = tmp_archive["path"]
    mode = (root / archive._resource_subdir).stat().st_mode
    assert tmp_archive["policy"]["access"]["umask"] == archive._default_umask
    assert mode & 0o7000 == 0o2000
    assert mode & archive._default_umask == 0


def test_store_and_find_resource(tmp_archive, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
    # assertions
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    mode = path.stat().st_mode
    assert mode & tmp_archive["policy"]["access"]["umask"] == 0
    assert path.read_text() == contents


def test_store_and_find_named_resource(tmp_archive, tmp_path):
    name = "dummy_2"
    src = tmp_path / "tempfile"
    contents = '{"foo": 20}\n'
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
    # assertions
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    assert path.name == name
    assert path.read_text() == contents


def test_store_and_find_resource_with_extension(tmp_archive, tmp_path):
    name = "dummy_3"
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
    # assertions
    path = archive.resource_path(tmp_archive, name, resolve_ext=True)
    assert path.is_file()
    assert path.stem == name
    assert path.suffix == src.suffix
    assert path.read_text() == contents


def test_cannot_store_duplicate_resource(tmp_archive, tmp_path):
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src)
    src.write_text(contents)
    with pytest.raises(KeyError):
        archive.store_resource(tmp_archive, src)


def test_cannot_store_duplicate_basenames(tmp_archive, tmp_path):
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src)
    path = archive.resource_path(tmp_archive, "temp", resolve_ext=True)
    assert path.name == src.name
    src.write_text(contents)
    with pytest.raises(KeyError):
        archive.store_resource(tmp_archive, src, "temp.txt")


def test_cannot_violate_directory_policy(tmp_archive, tmp_path):
    dir = tmp_path / "tempdir"
    dir.mkdir()
    with pytest.raises(TypeError):
        archive.store_resource(tmp_archive, dir)


def test_can_store_directories(tmp_dir_archive, tmp_path):
    id = "dummy_1"
    umask = tmp_dir_archive["policy"]["access"]["umask"]
    dname = tmp_path / "tempdir"
    fname = dname / "tempfile"
    dname.mkdir()
    fname.write_text("this is dumb")
    fname.chmod(0o777)
    assert (fname.stat().st_mode & umask) != 0

    archive.store_resource(tmp_dir_archive, dname, id)
    path = archive.resource_path(tmp_dir_archive, id, resolve_ext=True)
    assert path.is_dir()
    assert (path.stat().st_mode & umask) == 0

    fpath = path / "tempfile"
    assert fpath.is_file()
    assert (fpath.stat().st_mode & umask) == 0


def test_can_strip_extensions(tmp_noext_archive, tmp_path):
    name = "dummy_3"
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_noext_archive, src, name)
    path = archive.resource_path(tmp_noext_archive, name, resolve_ext=True)
    assert path.exists()
    assert path.suffix == ""


def test_check_permissions(tmp_archive, tmp_path):
    name = "dummy_100"
    src = tmp_path / "temp.wav"
    contents = "not a wave file"
    src.write_text(contents)
    archive.store_resource(tmp_archive, src, name)
    # need a second source file for check_permissions
    dummy = tmp_path / "dummy"
    dummy.write_text("blah")

    tgt_base = tmp_archive["path"] / archive._resource_subdir
    tgt_sub = tgt_base / archive.id_stub(name)
    mode = tgt_base.stat().st_mode
    tgt_base.chmod(0o500)
    assert not archive.check_permissions(tmp_archive, dummy)
    tgt_base.chmod(0o400)
    assert not archive.check_permissions(tmp_archive, dummy)
    tgt_base.chmod(mode)
    assert archive.check_permissions(tmp_archive, dummy)
    mode = tgt_sub.stat().st_mode
    tgt_sub.chmod(0o500)
    assert not archive.check_permissions(tmp_archive, dummy, name)
    tgt_sub.chmod(0o400)
    assert not archive.check_permissions(tmp_archive, dummy, name)
    tgt_sub.chmod(mode)
    assert archive.check_permissions(tmp_archive, dummy, name)
