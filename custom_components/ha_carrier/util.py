"""Utility helpers shared across Carrier integration modules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any, overload

from carrier_api import System
from homeassistant.core import callback

_LOGGER: logging.Logger = logging.getLogger(__name__)
REDACTED = "**REDACTED**"

HEAT_TYPES: list[str] = [
    "hp_heat",
    "electric_heat",
    "reheat",
    "loop_pump",
]

COOL_TYPES: list[str] = [
    "cooling",
]

FAN_TYPES: list[str] = [
    "fan",
    "fan_gas",
]


def has_heat(carrier_system: System) -> bool:
    """Return True if the Carrier system supports heat source selection."""
    return any(getattr(carrier_system.energy, heat_type, False) is True for heat_type in HEAT_TYPES)


def has_cool(carrier_system: System) -> bool:
    """Return True if the Carrier system supports cool source selection."""
    return any(getattr(carrier_system.energy, cool_type, False) is True for cool_type in COOL_TYPES)


def has_fan(carrier_system: System) -> bool:
    """Return True if the Carrier system supports fan mode selection."""
    return any(getattr(carrier_system.energy, fan_type, False) is True for fan_type in FAN_TYPES)


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
