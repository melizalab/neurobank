# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""Types used by other modules"""

from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from httpx import Client


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


@runtime_checkable
class RemoteResource(FetchableResource, Protocol):
    """A resource downloaded from another host. find_resources caches downloads in a
    directory for the host (host or host:port)."""

    host: str


Resource = FetchableResource | NonFetchableResource


@dataclass(frozen=True)
class LocationContext:
    """What a location scheme's class may use to build a resource for a location.

    alt_base replaces the directory that contains each neurobank archive (see
    archive.Resource). http_session is the registry's client, for fetching over http(s);
    it sends the registry's credentials only to the registry.
    """

    alt_base: Path | None = None
    http_session: "Client | None" = None


class LocationScheme(Protocol):
    """A class that builds resources for the locations of its schemes."""

    schemes: ClassVar[tuple[str, ...]]

    @classmethod
    def from_location(
        cls, location: Mapping[str, Any], context: LocationContext
    ) -> Resource | None:
        """Returns a resource for a registry location dict, or None if it can't be
        reached from this host."""
        ...


_S = TypeVar("_S", bound=type[LocationScheme])

_location_schemes: dict[str, type[LocationScheme]] = {}


def location_scheme(cls: _S) -> _S:
    """Class decorator that registers cls (a LocationScheme) to build resources for its
    location schemes. Raises ValueError if a scheme already has a class."""
    for scheme in cls.schemes:
        existing = _location_schemes.get(scheme)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"location scheme '{scheme}' is already handled by {existing}"
            )
        _location_schemes[scheme] = cls
    return cls


def location_class(scheme: str) -> type[LocationScheme] | None:
    """Returns the class registered for a location scheme, or None."""
    return _location_schemes.get(scheme)
