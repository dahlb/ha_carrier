"""Utility helpers shared across Carrier integration modules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any, overload

from homeassistant.core import callback

_LOGGER: logging.Logger = logging.getLogger(__name__)
REDACTED = "**REDACTED**"


@overload
def async_redact_data(data: list[Any], to_redact: Iterable[Any]) -> list[Any]: ...


@overload
def async_redact_data(data: Mapping[Any, Any], to_redact: Iterable[Any]) -> dict[Any, Any]: ...


@overload
def async_redact_data[T](data: T, to_redact: Iterable[Any]) -> T: ...


@callback
def async_redact_data(data: Any, to_redact: Iterable[Any]) -> Any:
    """Recursively redact selected keys from mapping and list structures.

    Args:
        data: Original value that may contain nested mappings or lists.
        to_redact: Iterable of keys that should be replaced with a redaction marker.

    Returns:
        Any: Copy of the original data with sensitive values redacted.
    """
    if not isinstance(data, Mapping | list):
        return data

    if isinstance(data, list):
        return [async_redact_data(val, to_redact) for val in data]

    redacted = {**data}

    for key, value in redacted.items():
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        if key in to_redact:
            redacted[key] = REDACTED
        elif isinstance(value, Mapping):
            redacted[key] = async_redact_data(value, to_redact)
        elif isinstance(value, list):
            redacted[key] = [async_redact_data(item, to_redact) for item in value]

    return redacted
