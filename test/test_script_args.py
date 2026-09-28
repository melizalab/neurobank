# -*- mode: python -*-
import argparse

import pytest

from nbank.script import userpwd


def test_userpwd():
    assert userpwd("user:secret") == ("user", "secret")


def test_userpwd_password_with_colons():
    assert userpwd("user:pa:ss:word") == ("user", "pa:ss:word")


def test_userpwd_without_colon():
    with pytest.raises(argparse.ArgumentTypeError):
        userpwd("user")
