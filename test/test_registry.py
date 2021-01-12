# -*- coding: utf-8 -*-
# -*- mode: python -*-
from __future__ import division
from __future__ import unicode_literals

from unittest import TestCase

from nbank import registry, core

base = "https://example.org/neurobank/"
id   = "adfkj"
full = "https://example.org/neurobank/resources/adfkj/"

class ResourceNameTestCase(TestCase):

    def test_short_to_full(self):
        self.assertEqual(core.full_url(id, base), full)

    def test_short_to_full_noslash(self):
        self.assertEqual(core.full_url(id, base.rstrip("/")), full)

    def test_full_to_parts(self):
        B, I = core.parse_resource_id(full)
        self.assertEqual(B, base)
        self.assertEqual(I, id)

    def test_full_to_parts_noslash(self):
        B, I = core.parse_resource_id(full.rstrip("/"))
        self.assertEqual(B, base)
        self.assertEqual(I, id)

    def test_parts_to_parts(self):
        B, I = core.parse_resource_id(id, base)
        self.assertEqual(B, base)
        self.assertEqual(I, id)
