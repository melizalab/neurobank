# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""neurobank data management system"""

try:
    from importlib.metadata import version

    __version__ = version("neurobank")
except Exception:
    # If package is not installed (e.g. during development)
    __version__ = "unknown"


# Variables:
# End:
