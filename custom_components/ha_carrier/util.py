"""Utility helpers shared across Carrier integration modules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any, overload

from aiohttp import ClientError
from carrier_api import AuthError, BaseError, System
from gql.transport.exceptions import (
    TransportConnectionFailed,
    TransportError,
    TransportProtocolError,
    TransportQueryError,
    TransportServerError,
)
from homeassistant.core import callback

from .exceptions import CarrierUnauthorizedError

_LOGGER: logging.Logger = logging.getLogger(__name__)
REDACTED = "**REDACTED**"

HEAT_TYPES: list[str] = [
    "electric_heat",
    "gas",
    "hp_heat",
    "loop_pump",
    "reheat",
]

COOL_TYPES: list[str] = [
    "cooling",
    "loop_pump",
]

FAN_TYPES: list[str] = [
    "fan",
    "fan_gas",
]

ENERGY_METRIC_MAP: dict[str, str] = {
    "cooling": "coolingKwh",
    "electric_heat": "eHeatKwh",
    "fan_gas": "fanGasKwh",
    "fan": "fanKwh",
    "gas": "gasKwh",
    "hp_heat": "hPHeatKwh",
    "loop_pump": "loopPumpKwh",
    "reheat": "reheatKwh",
}

TIMESTAMP_TYPES: tuple[str, ...] = ("all_data", "websocket", "energy")


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


TRANSIENT_TRANSPORT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ClientError,
    TimeoutError,
    OSError,
    TransportConnectionFailed,
    TransportProtocolError,
    TransportQueryError,
)
"""Transport-layer exceptions that should retry with backoff."""

RECOVERABLE_REFRESH_EXCEPTIONS: tuple[type[BaseException], ...] = (
    *TRANSIENT_TRANSPORT_EXCEPTIONS,
    TransportError,
    AuthError,
    BaseError,
)
"""Exceptions a coordinator refresh may recover from on a later interval."""

WEBSOCKET_RECOVERABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    CarrierUnauthorizedError,
    *TRANSIENT_TRANSPORT_EXCEPTIONS,
    TransportError,
)
"""Exceptions the websocket reconnect loop should treat as recoverable."""


def _iter_exception_chain(error: BaseException) -> Iterable[BaseException]:
    """Yield the exception followed by its __cause__/__context__ chain.

    Args:
        error: Exception to walk.

    Yields:
        BaseException: Each exception in the chain, deduplicated.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_unauthorized_error(error: BaseException) -> bool:
    """Return True if any exception in the chain represents a 401-style failure.

    Args:
        error: Exception raised by the Carrier client or transport.

    Returns:
        bool: True when the error or one of its causes is a 401.
    """
    for current in _iter_exception_chain(error):
        if isinstance(current, CarrierUnauthorizedError | AuthError):
            return True
        if isinstance(current, TransportServerError):
            status_code = getattr(current, "code", None) or getattr(current, "status", None)
            if status_code == 401:
                return True
    return False


def is_transient_transport_error(error: BaseException) -> bool:
    """Return True if any exception in the chain is a transient transport error.

    Args:
        error: Exception raised by the Carrier client or transport.

    Returns:
        bool: True when the error or one of its causes is transient.
    """
    return any(
        isinstance(current, TRANSIENT_TRANSPORT_EXCEPTIONS)
        for current in _iter_exception_chain(error)
    )
