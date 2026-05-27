"""Utility helpers shared across Carrier integration modules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any, overload

from carrier_api import (
    ApiConnectionGraphql,
    CarrierApiAuthError,
    CarrierApiConnectionError,
    CarrierApiError,
    CarrierApiTokenRefreshError,
    CarrierApiWebsocketError,
    EnergyUsageMetric,
)
from homeassistant.core import callback

from .exceptions import CarrierUnauthorizedError

_LOGGER: logging.Logger = logging.getLogger(__name__)
REDACTED = "**REDACTED**"

TIMESTAMP_TYPES: tuple[str, ...] = ("all_data", "websocket", "energy")

# Transport-layer exceptions that should retry with backoff.
TRANSIENT_TRANSPORT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    CarrierApiConnectionError,
    CarrierApiTokenRefreshError,
    CarrierApiWebsocketError,
)

# Exceptions a coordinator refresh may recover from on a later interval.
RECOVERABLE_REFRESH_EXCEPTIONS: tuple[type[BaseException], ...] = (
    *TRANSIENT_TRANSPORT_EXCEPTIONS,
    CarrierApiAuthError,
    CarrierApiError,
)

# Transport exceptions a write should report as communication failures.
RECOVERABLE_WRITE_COMMUNICATION_EXCEPTIONS: tuple[type[BaseException], ...] = (
    *TRANSIENT_TRANSPORT_EXCEPTIONS,
)

# Exceptions the websocket reconnect loop should treat as recoverable.
WEBSOCKET_RECOVERABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    CarrierUnauthorizedError,
    CarrierApiAuthError,
    *TRANSIENT_TRANSPORT_EXCEPTIONS,
)

# Carrier websocket payload/data-shape errors that should trigger reconciliation.
WEBSOCKET_DATA_UPDATE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    KeyError,
    TypeError,
    ValueError,
)


def energy_metric_value(metric: EnergyUsageMetric | str) -> str:
    """Return the normalized value for a Carrier energy metric.

    Args:
        metric: Carrier API energy metric enum or string value.

    Returns:
        str: Normalized metric value used in unique IDs and helper lookups.
    """
    return metric.value if isinstance(metric, EnergyUsageMetric) else metric


async def async_get_carrier_identity_id(api_connection: ApiConnectionGraphql) -> str | None:
    """Return the Carrier identity ID for an authenticated API connection.

    ``async_get_carrier_identity_id`` calls ``api_connection.load_data`` and
    ``api_connection.get_user_info`` on the supplied client. ``None`` is only
    returned when the response payload is missing or has an unexpected shape;
    transport and authentication failures raised by those calls propagate to
    the caller.

    Args:
        api_connection: Connected Carrier API client to query.

    Returns:
        str | None: Non-empty Carrier ``identityId`` when the response shape is
            valid, otherwise ``None``.

    Raises:
        CarrierApiAuthError: Credentials were rejected by the Carrier API.
        CarrierApiError: The Carrier API client reported a non-auth error.
    """
    await api_connection.load_data()
    user_info = await api_connection.get_user_info()
    if not isinstance(user_info, dict) or not user_info:
        return None

    user_details = user_info.get("user")
    if not isinstance(user_details, dict) or not user_details:
        return None

    identity_id = user_details.get("identityId")
    if not isinstance(identity_id, str) or not identity_id:
        return None

    return identity_id


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


def _iter_exception_chain(error: BaseException) -> Iterable[BaseException]:
    """Yield the exception followed by its __cause__ and __context__ chains.

    Args:
        error: Exception to walk.

    Yields:
        BaseException: Each exception in the chain, deduplicated.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if current.__context__ is not None:
            stack.append(current.__context__)
        if current.__cause__ is not None:
            stack.append(current.__cause__)


def is_unauthorized_error(error: BaseException) -> bool:
    """Return True if any exception in the chain represents a 401-style failure.

    Args:
        error: Exception raised by the Carrier client or transport.

    Returns:
        bool: True when the error or one of its causes is a 401.
    """
    for current in _iter_exception_chain(error):
        if isinstance(current, CarrierUnauthorizedError | CarrierApiAuthError):
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
