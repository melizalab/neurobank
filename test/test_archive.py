# -*- coding: utf-8 -*-
# -*- mode: python -*-
from __future__ import division
from __future__ import unicode_literals

import os
import sys
import tempfile
import shutil
import json
from unittest import TestCase

from nbank import archive


class ArchiveTestBase(TestCase):

    def setUp(self):
        super(ArchiveTestBase, self).setUp()
        # make a scratch directory
        self.tmpd = tempfile.mkdtemp()
        self.root = os.path.join(self.tmpd, "archive")
        # create archive
        archive.create(self.root, "https://localhost:8000/neurobank")

    def tearDown(self):
        super(ArchiveTestBase, self).tearDown()
        # destroy temporary directory
        shutil.rmtree(self.tmpd)


class ArchiveTestCase(ArchiveTestBase):
    def test_can_read_config(self):
        cfgtmpl = json.loads(archive._nbank_json)
        cfg = archive.get_config(self.root)
        self.assertEqual(cfg['policy']['access']['umask'], archive._default_umask)
        self.assertEqual(cfg['path'], self.root)

    def test_resources_mode(self):
        mode = os.stat(os.path.join(self.root, archive._resource_subdir)).st_mode
        self.assertEqual(mode & 0o7000, 0o2000)
        self.assertEqual(mode & archive._default_umask, 0)


class ResourceTestCase(ArchiveTestBase):

    def setUp(self):
        super(ResourceTestCase, self).setUp()
        self.cfg = archive.get_config(self.root)

        # store a resource
        self.name = "dummy_1"
        self.contents = {"foo": 10}
        src = os.path.join(self.tmpd, self.name)
        with open(src, 'wt') as fp:
            json.dump(self.contents, fp)
        archive.store_resource(self.cfg, src)

    def test_find_resource(self):
        path = archive.find_resource(self.root, self.name)
        self.assertTrue(os.path.exists(path))
        mode = os.stat(path).st_mode
        self.assertEqual(mode & self.cfg['policy']['access']['umask'], 0)
        with open(path, 'rt') as fp:
            self.assertDictEqual(self.contents, json.load(fp))

    def test_store_named_resource(self):
        name = "dummy_2"
        src = os.path.join(self.tmpd, "tempfile")
        with open(src, 'wt') as fp:
            fp.write("this is dumb")
        archive.store_resource(self.cfg, src, name)
        path = archive.find_resource(self.root, name)
        self.assertTrue(os.path.exists(path))

    def test_can_store_resources_with_extensions(self):
        name = "dummy_3"
        src = os.path.join(self.tmpd, "temp.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        archive.store_resource(self.cfg, src, name)
        path = archive.find_resource(self.root, name)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.path.splitext(src)[1], os.path.splitext(path)[1])

    def test_cannot_store_duplicate_resource(self):
        src = os.path.join(self.tmpd, "tempfile")
        with open(src, 'wt') as fp:
            fp.write("this is dumb")
        with self.assertRaises(KeyError):
            archive.store_resource(self.cfg, src, self.name)

    def test_cannot_store_duplicate_basenames(self):
        src = os.path.join(self.tmpd, "temp.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        with self.assertRaises(KeyError):
            archive.store_resource(self.cfg, src, self.name)

    def test_cannot_violate_directory_policy(self):
        dname = os.path.join(self.tmpd, "tempdir")
        os.mkdir(dname)
        with self.assertRaises(TypeError):
            archive.store_resource(self.cfg, dname)


class DirectoryResourceTestCase(ArchiveTestBase):

    def setUp(self):
        super(DirectoryResourceTestCase, self).setUp()
        self.cfg = archive.get_config(self.root)
        # allow directories as resources
        self.cfg['policy']['allow_directories'] = True

    def test_can_store_directories(self):
        id = "dummy_1"
        dname = os.path.join(self.tmpd, "tempdir")
        fname = os.path.join(dname, "tempfile")
        os.mkdir(dname)
        with open(fname, 'wt') as fp:
            fp.write("this is dumb")
        os.chmod(fname, 0o777)
        mode = os.stat(fname).st_mode
        self.assertNotEqual(mode & self.cfg['policy']['access']['umask'], 0)

        archive.store_resource(self.cfg, dname, id)
        path = archive.find_resource(self.root, id)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.isdir(path))
        mode = os.stat(path).st_mode
        self.assertEqual(mode & self.cfg['policy']['access']['umask'], 0)

        fpath = os.path.join(path, "tempfile")
        self.assertTrue(os.path.exists(fpath))
        mode = os.stat(path).st_mode
        self.assertEqual(mode & self.cfg['policy']['access']['umask'], 0)


class StripExtensionTestCase(ArchiveTestBase):

    def setUp(self):
        super(StripExtensionTestCase, self).setUp()
        self.cfg = archive.get_config(self.root)
        # allow directories as resources
        self.cfg['policy']['keep_extensions'] = False

    def test_can_store_resources_with_extensions(self):
        name = "dummy_3"
        src = os.path.join(self.tmpd, "temp.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        archive.store_resource(self.cfg, src, name)
        path = archive.find_resource(self.root, name)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.path.splitext(path)[1], "")
