# -*- coding: utf-8 -*-
# -*- mode: python -*-
from __future__ import division
from __future__ import unicode_literals

import os
import tempfile
import shutil
import json
from unittest import TestCase

import requests as rq

import nbank
from nbank import registry, archive

class NeurobankTestCase(TestCase):
    """ Base test case for all integration tests

    To run this test, the NBANK_REGISTRY environment variable needs to be set, and
    a .netrc with username and password created
    """

    url = os.environ[nbank.env_registry]
    umask = 0o027
    dtype = "a_dtype"

    def setUp(self):
        super(NeurobankTestCase, self).setUp()
        # make a scratch directory
        self.tmpd = tempfile.mkdtemp()
        self.domain = os.path.basename(self.tmpd)
        self.root = os.path.join(self.tmpd, "archive")
        # add domain - this will fail if netrc is not set up.
        registry.add_domain(self.url, self.domain, registry._neurobank_scheme, self.root)
        # try adding a dtype
        try:
            registry.add_datatype(self.url, self.dtype, "content-type")
        except registry.rq.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                pass
        # create archive
        archive.create(self.root, self.url, self.umask)
        # edit the project policy so we don't hash everything by default
        cfg = archive.get_config(self.root)
        cfg["policy"]["require_hash"] = False
        with open(os.path.join(self.root, archive._config_fname), 'wt') as fp:
            json.dump(cfg, fp)

    def tearDown(self):
        super(NeurobankTestCase, self).tearDown()
        # destroy temporary directory
        shutil.rmtree(self.tmpd)

    def test_can_deposit_and_locate_resource(self):
        # create a dummy file
        src = os.path.join(self.tmpd, "temp.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        ids = tuple(nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True))
        self.assertEqual(len(ids), 1)
        locations = tuple(nbank.locate(self.url, ids[0]))
        self.assertEqual(len(locations), 1)

    def test_can_deposit_multiple_resources(self):
        src = [os.path.join(self.tmpd, f) for f in ("test1.txt", "test2.txt")]
        for s in src:
            with open(s, 'wt') as fp:
                fp.write("this is not a wave file")
        ids = tuple(nbank.deposit(self.root, src, dtype=self.dtype, auto_id=True))
        self.assertEqual(len(ids), len(src))

    def test_cannot_deposit_with_duplicate_hash(self):
        src = [os.path.join(self.tmpd, f) for f in ("test1.txt", "test2.txt")]
        for s in src:
            with open(s, 'wt') as fp:
                fp.write("this is not a wave file")
        with self.assertRaises(rq.exceptions.HTTPError):
            tuple(nbank.deposit(self.root, src, hash=True, dtype=self.dtype, auto_id=True))

    def test_can_deposit_with_metadata(self):
        # create a dummy file
        src = os.path.join(self.tmpd, "tempz.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        metadata = {"blah": "1", "bleh": "abcd"}
        ids = tuple(nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True, **metadata))
        self.assertEqual(len(ids), 1)
        info = registry.get_resource(self.url, ids[0])
        self.assertDictEqual(info["metadata"], metadata)

    def test_invalid_registry_url_exception(self):
        with self.assertRaises(rq.exceptions.ConnectionError):
            tuple(nbank.locate("http://nosuchurl/nosuchendpoint", "blahblah"))

    def test_cannot_deposit_invalid_datatype(self):
        src = os.path.join(self.tmpd, "tempy.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        with self.assertRaises(rq.exceptions.HTTPError):
            tuple(nbank.deposit(self.root, [src], dtype="nosuchdtype", auto_id=True))

    def test_skip_duplicate_id(self):
        src = os.path.join(self.tmpd, "dupl.txt")
        with open(src, 'wt') as fp:
            fp.write("this is a text file")
        ids = tuple(nbank.deposit(self.root, [src], self.dtype, auto_id=True))
        dids = tuple(nbank.deposit(self.root, ids, dtype=self.dtype))
        self.assertEqual(len(dids), 0)

    def test_skip_directories(self):
        dname = os.path.join(self.tmpd, "tempdir")
        os.mkdir(dname)
        ids = tuple(nbank.deposit(self.root, [dname], self.dtype, auto_id=True))
        self.assertEqual(len(ids), 0)
