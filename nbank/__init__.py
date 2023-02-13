# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""neurobank data management system

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Mon Nov 25 08:43:19 2013
"""
from __future__ import absolute_import

__version__ = "0.10.0"

from nbank.core import (
    deposit,
    search,
    find,
    get,
    describe,
    get_archive,
    verify,
)

from nbank.registry import (
    fetch_resource,
)

# Variables:
# End:
