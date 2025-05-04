# -*- mode: python -*-
"""neurobank data management system

Copyright (C) 2013--2024 Dan Meliza <dan@meliza.org>
"""

try:
    from importlib.metadata import version

    __version__ = version("neurobank")
except Exception:
    # If package is not installed (e.g. during development)
    __version__ = "unknown"


# Variables:
# End:
