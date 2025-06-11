# -*- mode: python -*-
"""functions for managing a tape-based data archive

Copyright (C) 2025 Dan Meliza <dan@meliza.org>
"""

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("nbank")  # root logger


class Resource:
    """A resource stored on a tape.

    The `root` field of the location is interpreted as
    `name_of_tape`:`file_index`. The `alt_base` parameter can be set to point to
    a tar file on a local file system.

    """

    local: False

    def __init__(self, root: str, id: str, alt_base: Optional[Path] = None):
        try:
            self.tape_name, file_index = root.split(":")
            self.file_index = int(file_index)
        except ValueError as err:
            raise ValueError("Tape resources must have the form 'name:index'") from err
        self.alt_base = alt_base
        self.name = id
        # TODO: set local to True if alt_base is set?

    def __str__(self):
        return f"tape://{self.tape_name}:{self.file_index}/{self.name}"

    # TODO: implement fetch when alt_base is set?
