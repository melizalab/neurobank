# -*- coding: utf-8 -*-
# -*- mode: python -*-
import pytest

import responses
from responses import matchers

from nbank import core, registry, archive, util

from test.test_registry import (
    base_url,
    resource_url,
    info_url,
    datatypes_url,
    archives_url,
)

archive_name = "archive"


def random_string(N):
    import random
    import string

    return "".join(
        random.SystemRandom().choice(string.ascii_uppercase + string.digits)
        for _ in range(N)
    )


@pytest.fixture
def tmp_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(root, base_url, umask=0o027, require_hash=False)


@pytest.fixture
def tmp_uuid_archive(tmp_path):
    root = tmp_path / "archive"
    return archive.create(
        root, base_url, umask=0o027, auto_id_type="uuid", require_hash=False
    )


@pytest.fixture
def mocked_resps():
    with responses.RequestsMock() as rsps:
        yield rsps


def test_deposit_resource(mocked_resps, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    metadata = {"experimenter": "dmeliza"}
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    mocked_resps.get(
        archives_url,
        json=[{"name": archive_name, "root": str(root)}],
        match=[
            matchers.query_param_matcher({"scheme": "neurobank", "root": str(root)})
        ],
    )
    mocked_resps.post(
        resource_url,
        json={"name": name},
        match=[
            matchers.json_params_matcher(
                {
                    "name": name,
                    "dtype": dtype,
                    "locations": [archive_name],
                    "sha1": sha1,
                    "metadata": metadata,
                }
            )
        ],
    )
    items = list(core.deposit(root, files=[src], dtype=dtype, **metadata))
    assert items == [{"source": src, "id": name}]


def test_deposit_resource_errors(mocked_resps, tmp_archive, tmp_path):
    root = tmp_archive["path"]
    name = "dummy_1"
    dtype = "dummy-dtype"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    mocked_resps.get(
        archives_url,
        json=[],
        match=[
            matchers.query_param_matcher({"scheme": "neurobank", "root": str(root)})
        ],
    )
    with pytest.raises(ValueError):
        _ = list(core.deposit(tmp_path, files=[src], dtype=dtype))

    # archive not in registry
    with pytest.raises(RuntimeError):
        _ = list(core.deposit(root, files=[src], dtype=dtype))


## test failure cases for deposit: archive not in registry, src does not exist,
## duplicate hash/id


def test_describe_resource(mocked_resps):
    name = "dummy_2"
    data = {"you": "found me"}
    mocked_resps.get(registry.full_url(base_url, name), json=data)
    info = core.describe(base_url, name)
    assert info == data


def test_describe_nonexistent_resource(mocked_resps):
    name = "dummy_2"
    data = {"detail": "not found"}
    mocked_resps.get(registry.full_url(base_url, name), json=data, status=404)
    info = core.describe(base_url, name)
    assert info is None


def test_search_resource(mocked_resps):
    data = [{"super": "great!"}, {"also": "awesome!"}]
    query = {"sha1": "abc23"}
    mocked_resps.get(
        resource_url, json=data, match=[matchers.query_param_matcher(query)]
    )
    items = list(core.search(base_url, **query))
    assert items == data


def test_search_nonexistent_resource(mocked_resps):
    data = []
    query = {"sha1": "abc23a"}
    mocked_resps.get(
        resource_url, json=data, match=[matchers.query_param_matcher(query)]
    )
    items = list(core.search(base_url, **query))
    assert items == data


def test_find_resource_location(mocked_resps):
    from nbank.archive import resource_path

    name = "dummy_3"
    mocked_resps.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/"),
        json=[
            {
                "scheme": "neurobank",
                "root": "/home/data/starlings",
                "resource_name": name,
            }
        ],
    )
    items = list(core.find(base_url, name))
    assert items == [resource_path("/home/data/starlings", name)]
    item = core.get(base_url, name)
    assert item == resource_path("/home/data/starlings", name)


def test_find_resource_location_nonexistent(mocked_resps):
    name = "dummy_4"
    mocked_resps.get(
        registry.url_join(registry.full_url(base_url, name) + "locations/"), json=[]
    )
    item = core.get(base_url, name)
    assert item is None


def test_verify_resource_by_hash(mocked_resps, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = [{"sha1": sha1}]
    query = {"sha1": sha1}
    mocked_resps.get(
        resource_url, json=data, match=[matchers.query_param_matcher(query)]
    )
    items = list(core.verify(base_url, src))
    assert items == data


def test_verify_resource_by_id(mocked_resps, tmp_path):
    name = "dummy_1"
    src = tmp_path / name
    contents = '{"foo": 10}\n'
    src.write_text(contents)
    sha1 = util.hash(src)
    data = {"sha1": sha1}
    mocked_resps.get(registry.full_url(base_url, name), json=data)
    assert core.verify(base_url, src, name)


# class NeurobankTestCase(TestCase):
#     """Base test case for all integration tests

#     To run this test, the NBANK_REGISTRY environment variable needs to be set, and
#     a .netrc with username and password created
#     """

#     url = nbank.default_registry()
#     umask = 0o027
#     dtype = "a_dtype"

#     def setUp(self):
#         super(NeurobankTestCase, self).setUp()
#         # make a scratch directory
#         self.tmpd = tempfile.mkdtemp()
#         self.archive = os.path.basename(self.tmpd)
#         self.root = os.path.join(self.tmpd, "archive")
#         # add archive - this will fail if netrc is not set up.
#         registry.add_archive(
#             self.url, self.archive, registry._neurobank_scheme, self.root
#         )
#         # try adding a dtype
#         try:
#             registry.add_datatype(self.url, self.dtype, "content-type")
#         except registry.rq.exceptions.HTTPError as e:
#             if e.response.status_code == 400:
#                 pass
#         # create archive
#         archive.create(self.root, self.url, self.umask)
#         # edit the project policy so we don't hash everything by default
#         cfg = archive.get_config(self.root)
#         cfg["policy"]["require_hash"] = False
#         with open(os.path.join(self.root, archive._config_fname), "wt") as fp:
#             json.dump(cfg, fp)

#     def tearDown(self):
#         super(NeurobankTestCase, self).tearDown()
#         # destroy temporary directory
#         shutil.rmtree(self.tmpd)


# class DefaultAutoIdNeurobankTestCase(NeurobankTestCase):
#     def test_can_deposit_and_locate_resource(self):
#         # create a dummy file
#         src = os.path.join(self.tmpd, "temp.wav")
#         with open(src, "wt") as fp:
#             fp.write(random_string(64))
#         ids = tuple(nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True))
#         self.assertEqual(len(ids), 1)
#         locations = tuple(registry.get_locations(self.url, ids[0]["id"]))
#         self.assertEqual(len(locations), 1)

#     def test_can_deposit_multiple_resources(self):
#         src = [os.path.join(self.tmpd, f) for f in ("test1.txt", "test2.txt")]
#         for s in src:
#             with open(s, "wt") as fp:
#                 fp.write(random_string(64))
#         ids = tuple(nbank.deposit(self.root, src, dtype=self.dtype, auto_id=True))
#         self.assertEqual(len(ids), len(src))

#     def test_cannot_deposit_with_duplicate_hash(self):
#         src = [os.path.join(self.tmpd, f) for f in ("test1.txt", "test2.txt")]
#         for s in src:
#             with open(s, "wt") as fp:
#                 fp.write("this is not a wave file")
#         with self.assertRaises(rq.exceptions.HTTPError):
#             tuple(
#                 nbank.deposit(self.root, src, hash=True, dtype=self.dtype, auto_id=True)
#             )

#     def test_can_deposit_with_metadata(self):
#         # create a dummy file
#         src = os.path.join(self.tmpd, "tempz.wav")
#         with open(src, "wt") as fp:
#             fp.write(random_string(64))
#         metadata = {"blah": "1", "bleh": "abcd"}
#         ids = tuple(
#             nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True, **metadata)
#         )
#         self.assertEqual(len(ids), 1)
#         info = registry.get_resource(self.url, ids[0]["id"])
#         self.assertDictEqual(info["metadata"], metadata)

#     def test_can_update_metadata(self):
#         src = os.path.join(self.tmpd, "temp-edit-medata.wav")
#         with open(src, "wt") as fp:
#             fp.write(random_string(64))
#         metadata = {"blah": "1", "bleh": "abcd"}
#         ids = tuple(nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True))
#         self.assertEqual(len(ids), 1)
#         rep = registry.update_resource_metadata(self.url, ids[0]["id"], **metadata)
#         self.assertDictEqual(rep["metadata"], metadata)
#         info = registry.get_resource(self.url, ids[0]["id"])
#         self.assertDictEqual(info["metadata"], metadata)

#     def test_invalid_registry_url_exception(self):
#         with self.assertRaises(rq.exceptions.ConnectionError):
#             tuple(registry.get_locations("http://nosuchurl/nosuchendpoint", "blahblah"))

#     def test_cannot_deposit_invalid_datatype(self):
#         src = os.path.join(self.tmpd, "tempy.wav")
#         with open(src, "wt") as fp:
#             fp.write("this is not a wave file")
#         with self.assertRaises(rq.exceptions.HTTPError):
#             tuple(nbank.deposit(self.root, [src], dtype="nosuchdtype", auto_id=True))

#     def test_skip_duplicate_id(self):
#         src = os.path.join(self.tmpd, "dupl.txt")
#         with open(src, "wt") as fp:
#             fp.write("this is a text file")
#         ids = tuple(
#             x["id"] for x in nbank.deposit(self.root, [src], self.dtype, auto_id=True)
#         )
#         dids = tuple(nbank.deposit(self.root, ids, dtype=self.dtype))
#         self.assertEqual(len(dids), 0)

#     def test_skip_directories(self):
#         dname = os.path.join(self.tmpd, "tempdir")
#         os.mkdir(dname)
#         ids = tuple(nbank.deposit(self.root, [dname], self.dtype, auto_id=True))
#         self.assertEqual(len(ids), 0)

#     def test_locate_nonexistent_resource(self):
#         result = tuple(registry.get_locations(self.url, "blahblah"))
#         self.assertEqual(len(result), 0)

#     def test_locate_invalid_resource(self):
#         result = tuple(registry.get_locations(self.url, "blahblah.2.2"))
#         self.assertEqual(len(result), 0)

#     def test_can_verify_hash(self):
#         src = os.path.join(self.tmpd, "hashable.txt")
#         with open(src, "wt") as fp:
#             fp.write(random_string(128))
#         ids = tuple(
#             nbank.deposit(self.root, [src], hash=True, dtype=self.dtype, auto_id=True)
#         )
#         self.assertEqual(len(ids), 1)
#         locations = tuple(registry.get_locations(self.url, ids[0]["id"]))
#         self.assertEqual(len(locations), 1)
#         path = archive.find_resource(nbank.get_archive(locations[0]))
#         # search by id
#         self.assertTrue(nbank.verify(path, self.url, id=ids[0]["id"]))
#         # search by hash
#         resources = tuple(nbank.verify(path, self.url))
#         self.assertEqual(len(resources), 1)
#         self.assertEqual(resources[0]["name"], ids[0]["id"])


# class UUIDAutoIdNeurobankTestCase(NeurobankTestCase):
#     """Test locally assigned auto identifiers"""

#     def setUp(self):
#         super(UUIDAutoIdNeurobankTestCase, self).setUp()
#         # edit the project policy for UUID auto identifiers
#         cfg = archive.get_config(self.root)
#         cfg["policy"]["auto_id_type"] = "uuid"
#         with open(os.path.join(self.root, archive._config_fname), "wt") as fp:
#             json.dump(cfg, fp)

#     def test_can_deposit_and_locate_resource(self):
#         import uuid

#         # create a dummy file
#         src = os.path.join(self.tmpd, "temp.wav")
#         with open(src, "wt") as fp:
#             fp.write("this is not a wave file")
#         ids = tuple(nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True))
#         self.assertEqual(len(ids), 1)
#         id = ids[0]["id"]
#         # this will raise an error if the identifier is not a valid UUID:
#         uuid.UUID(id)
#         locations = tuple(registry.get_locations(self.url, id))
#         self.assertEqual(len(locations), 1)


# class DirectoryNeurobankTestCase(NeurobankTestCase):
#     """Test handling of directory resources"""

#     dtype = "d_dtype"

#     def setUp(self):
#         super(DirectoryNeurobankTestCase, self).setUp()
#         # add a different dtype
#         try:
#             registry.add_datatype(self.url, self.dtype, "content-type")
#         except registry.rq.exceptions.HTTPError as e:
#             if e.response.status_code == 400:
#                 pass
#         # edit the project policy
#         cfg = archive.get_config(self.root)
#         cfg["policy"]["allow_directories"] = True
#         with open(os.path.join(self.root, archive._config_fname), "wt") as fp:
#             json.dump(cfg, fp)

#     def test_can_deposit_and_locate_directories(self):
#         dname = os.path.join(self.tmpd, "tempdir")
#         os.mkdir(dname)
#         ids = tuple(nbank.deposit(self.root, [dname], self.dtype, auto_id=True))
#         self.assertEqual(len(ids), 1)
#         locations = tuple(registry.get_locations(self.url, ids[0]["id"]))
#         self.assertEqual(len(locations), 1)

#     def test_can_hash_directories(self):
#         import uuid

#         dname = os.path.join(self.tmpd, "tempdir")
#         os.mkdir(dname)
#         with open(os.path.join(dname, "file"), "wt") as fp:
#             fp.write(str(uuid.uuid4()))
#         ids = tuple(
#             nbank.deposit(self.root, [dname], hash=True, dtype=self.dtype, auto_id=True)
#         )
#         self.assertEqual(len(ids), 1)

#     def test_cannot_deposit_with_duplicate_hash(self):
#         import uuid

#         uu = str(uuid.uuid4())
#         dnames = [os.path.join(self.tmpd, d) for d in ("tempdir1", "tempdir2")]
#         for d in dnames:
#             os.mkdir(d)
#             with open(os.path.join(d, "file"), "wt") as fp:
#                 fp.write(uu)
#         with self.assertRaises(rq.exceptions.HTTPError):
#             tuple(
#                 nbank.deposit(
#                     self.root, dnames, hash=True, dtype=self.dtype, auto_id=True
#                 )
#             )
