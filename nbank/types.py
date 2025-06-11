# -*- mode: python -*-
"""Types used by other modules"""

from abc import abstractmethod
from pathlib import Path
from typing import Protocol, Union


class NotFetchableError(Exception):
    pass


class NonFetchableResource:
    """A resource that can't be fetched (e.g., in an archive on tape)"""

    pass


class FetchableResource(Protocol):
    """A resource that can be fetched from a local or remote location"""

    @abstractmethod
    def fetch(self, target: Path) -> Path:
        """Copies or downloads the resource to target directory or file. Returns target path or raises an error"""
        pass


class LocalResource(FetchableResource, Protocol):
    """A local resource that can be linked or referred to by path"""

    path: Path

    @abstractmethod
    def link(self, target: Path) -> Path:
        """Links the resource to a target directory or file. Returns target path or raises an error"""
        pass


Resource = Union[FetchableResource, NonFetchableResource]
