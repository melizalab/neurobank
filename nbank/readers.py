# -*- mode: python -*-
"""Metadata readers: plugins that read registry metadata from deposited files.

A reader is a function `read_metadata(path) -> dict` registered as an entry point
in the group `nbank.metadata_readers`, named for the dtype it reads. It raises
ValueError if the file isn't a valid file of that dtype. Readers belong to the
packages that produce the files, and don't import neurobank.
"""

import json
import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

log = logging.getLogger("nbank")

ENTRY_POINT_GROUP = "nbank.metadata_readers"

Reader = Callable[[Path], dict[str, Any]]


def find_reader(dtype: str) -> Reader | None:
    """Returns the reader for dtype, or None if no installed package has one.

    Raises RuntimeError if more than one package has a reader for dtype, or the
    reader can't be loaded.
    """
    found = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == dtype]
    if not found:
        return None
    if len(found) > 1:
        packages = ", ".join(sorted(package_name(ep) for ep in found))
        raise RuntimeError(f"more than one metadata reader for '{dtype}': {packages}")
    (ep,) = found
    try:
        reader = ep.load()
    except Exception as err:
        raise RuntimeError(
            f"can't load the metadata reader for '{dtype}' from "
            f"{package_name(ep)}: {err} (deposit without reading file metadata "
            "to skip it)"
        ) from err
    if not callable(reader):
        raise RuntimeError(
            f"the metadata reader for '{dtype}' from {package_name(ep)} is not callable"
        )
    version = getattr(ep.dist, "version", None)
    log.debug(
        "   metadata reader for %s: %s (%s%s)",
        dtype,
        ep.value,
        package_name(ep),
        f" {version}" if version else "",
    )
    return reader


def package_name(ep) -> str:
    return ep.dist.name if ep.dist is not None else ep.value


def read_file_metadata(reader: Reader, dtype: str, path: Path) -> dict[str, Any]:
    """Runs a reader on path and checks what it returns.

    Raises ValueError if the file isn't valid for dtype, the reader fails, or it
    returns something that can't go to the registry. Keys with None values are
    left out.
    """
    try:
        metadata = reader(path)
    except ValueError as err:
        raise ValueError(f"not a valid {dtype} file: {err}") from err
    except Exception as err:
        log.debug("   the metadata reader for %s failed", dtype, exc_info=True)
        raise ValueError(
            f"the metadata reader for {dtype} failed: {type(err).__name__}: {err}"
        ) from err
    if not isinstance(metadata, dict):
        raise ValueError(
            f"the metadata reader for {dtype} returned {type(metadata).__name__}, "
            "not a dict"
        )
    out = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise ValueError(
                f"the metadata reader for {dtype} returned a key that isn't a "
                f"string: {key!r}"
            )
        if value is None:
            log.debug("   %s: no value from the file", key)
            continue
        try:
            # e.g. numpy scalars or arrays from HDF5 attributes
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"the metadata reader for {dtype} returned a value for '{key}' "
                f"that can't be stored: {value!r} ({type(value).__name__})"
            ) from err
        out[key] = value
    return out


def merge_metadata(
    from_file: dict[str, Any], given: dict[str, Any], *, override: bool = False
) -> dict[str, Any]:
    """Merges metadata read from a file with metadata given for the deposit.

    Raises ValueError if a key has different values in the two, unless override,
    in which case the given value is used.
    """
    conflicts = [
        key
        for key in from_file.keys() & given.keys()
        if not same_value(from_file[key], given[key])
    ]
    if conflicts and not override:
        details = "; ".join(
            f"{key}: {from_file[key]!r} in the file, {given[key]!r} given"
            for key in sorted(conflicts)
        )
        raise ValueError(f"given metadata doesn't match the file ({details})")
    for key in sorted(conflicts):
        log.info(
            "   %s: using %r instead of %r from the file",
            key,
            given[key],
            from_file[key],
        )
    return {**from_file, **given}


def same_value(a, b) -> bool:
    """True if a and b are equal, counting a boolean and a number as different
    (Python has True == 1)."""
    return a == b and isinstance(a, bool) == isinstance(b, bool)


__all__: list[str] = []
