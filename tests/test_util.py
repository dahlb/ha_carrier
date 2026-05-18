"""Pytest coverage for Carrier utility helpers."""

from __future__ import annotations

from collections.abc import Callable

from aiohttp import ClientError
from gql.transport.exceptions import TransportServerError
import pytest

from custom_components.ha_carrier.util import (
    async_redact_data,
    is_transient_transport_error,
    is_unauthorized_error,
)


@pytest.mark.parametrize(
    ("classifier", "nested_error"),
    [
        (is_unauthorized_error, TransportServerError("unauthorized", code=401)),
        (is_transient_transport_error, ClientError("temporary")),
    ],
)
def test_exception_classifiers_inspect_context_when_cause_exists(
    classifier: Callable[[BaseException], bool],
    nested_error: BaseException,
) -> None:
    """Find classified errors on the context branch of an exception graph."""
    error = RuntimeError("root")
    error.__cause__ = ValueError("non-matching cause")
    error.__context__ = nested_error

    assert classifier(error) is True


def test_async_redact_data_recursively_redacts_mappings_and_lists() -> None:
    """Redact sensitive keys while preserving unrelated nested payload data."""
    payload = {"username": "user", "nested": [{"password": "secret", "mode": "auto"}]}

    redacted = async_redact_data(payload, {"username", "password"})

    assert redacted == {
        "username": "**REDACTED**",
        "nested": [{"password": "**REDACTED**", "mode": "auto"}],
    }
    assert payload["username"] == "user"
