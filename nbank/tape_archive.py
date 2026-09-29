# -*- mode: python -*-
"""functions for managing a tape-based data archive

Copyright (C) 2025 Dan Meliza <dan@meliza.org>
"""

import logging
from pathlib import Path

from nbank.types import location_scheme

log = logging.getLogger("nbank")  # root logger


@location_scheme
class Resource:
    """A resource stored on a tape.

    The `root` field of the location is interpreted as
    `name_of_tape`:`file_index`. The `alt_base` parameter can be set to point to
    a tar file on a local file system. `member` is the name of the tar member
    that holds the resource, if the location records one; otherwise the member
    is the one whose name (without extension) is the resource id.

    """

    schemes = ("tape",)
    local = False

    def __init__(
        self,
        root: str,
        id: str,
        alt_base: Path | None = None,
        member: str | None = None,
    ):
        try:
            self.tape_name, file_index = root.split(":")
            self.file_index = int(file_index)
        except ValueError as err:
            raise ValueError("Tape resources must have the form 'name:index'") from err
        self.alt_base = alt_base
        self.id = id
        self.member = member
        # TODO: set local to True if alt_base is set?

    @classmethod
    def from_location(cls, location, *, alt_base=None, http_session=None):
        return cls(
            location["root"],
            location["resource_name"],
            alt_base,
            member=location.get("key"),
        )

    def __str__(self):
        return f"tape://{self.tape_name}:{self.file_index}/{self.id}"

    def __repr__(self):
        return f"<tape-archive resource: {self.id} @ {self}>"

    # TODO: implement fetch when alt_base is set?
