# -*- coding: utf-8 -*-
# -*- mode: python -*-

from test.common import *

from neurobank import nbank


def test_update_json_data():
    test_map = { 'a' : "a value",
                 'b' : [1, 2, 3],
                 'c' : {'d' : 5}
             }
    # must throw error if scalar doesn't match
    nbank.update_json_data(test_map, a='a value')
    with assert_raises(ValueError):
        nbank.update_json_data(test_map, a='a different value')

    nbank.update_json_data(test_map, d='cow', b=[4], c={'d' : 6, 'e': 7})
    assert_equal(test_map['d'], 'cow', "missing value not added to mapping")
    assert_equal(test_map['b'], [1,2,3,4], "list not extended")
    assert_equal(test_map['c'], {'d': 6, 'e': 7}, "dict not updated")
