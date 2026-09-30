# -*- mode: python -*-
"""Types used by other modules"""

from abc import abstractmethod
from pathlib import Path
from typing import Protocol


class NotFetchableError(Exception):
    pass


class NonFetchableResource:
    """A resource that can't be fetched (e.g., in an archive on tape)"""

    pass


class FetchableResource(Protocol):
    """A resource that can be fetched from a local or remote location"""

    @abstractmethod
    def fetch(self, target: Path) -> Path:
        """Copies or downloads the resource to target; returns the path written."""
        pass


class LocalResource(FetchableResource, Protocol):
    """A local resource that can be linked or referred to by path"""

    path: Path

    @abstractmethod
    def link(self, target: Path) -> Path:
        """Links the resource into target; returns the path of the link."""
        pass


Resource = FetchableResource | NonFetchableResource

_location_schemes: dict[str, type] = {}


def location_scheme(cls: type) -> type:
    """Class decorator that registers cls to build resources for its location schemes.

    cls needs a `schemes` tuple and a classmethod `from_location(location, *, alt_base,
    http_session)` that returns a resource, or None if it can't be reached from this
    host. Raises ValueError if a scheme already has a class.
    """
    for scheme in cls.schemes:
        existing = _location_schemes.get(scheme)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"location scheme '{scheme}' is already handled by {existing}"
            )
        _location_schemes[scheme] = cls
    return cls


def location_class(scheme: str) -> type | None:
    """Returns the class registered for a location scheme, or None."""
    return _location_schemes.get(scheme)
