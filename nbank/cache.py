# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: BSD-3-Clause
"""Local cache of downloaded resources"""

import os
import sys
from pathlib import Path

_env_cache = "NBANK_CACHE"


def cache_dir() -> Path:
    """Returns the cache directory: NBANK_CACHE if it's set, otherwise neurobank
    in the platform's user cache directory."""
    if path := os.environ.get(_env_cache):
        return Path(path)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "neurobank"


__all__ = ["cache_dir"]
