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

    def test_can_deposit_with_metadata(self):
        # create a dummy file
        src = os.path.join(self.tmpd, "temp.wav")
        with open(src, 'wt') as fp:
            fp.write("this is not a wave file")
        metadata = {"blah": "1", "bleh": "abcd"}
        ids = tuple(nbank.deposit(self.root, [src], dtype=self.dtype, auto_id=True, **metadata))
        self.assertEqual(len(ids), 1)
        info = registry.get_resource(self.url, ids[0])
        self.assertDictEqual(info["metadata"], metadata)
