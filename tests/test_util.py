"""Pytest coverage for Carrier utility helpers."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from carrier_api import (
    CarrierApiAuthError,
    CarrierApiConnectionError,
    CarrierApiGraphqlError,
    CarrierApiTokenRefreshError,
    CarrierApiWebsocketError,
)
import pytest

from custom_components.ha_carrier.util import (
    async_redact_data,
    is_transient_transport_error,
    is_unauthorized_error,
)

INTEGRATION_ROOT = Path(__file__).parents[1] / "custom_components" / "ha_carrier"
DEPRECATED_CARRIER_API_EXCEPTIONS = {"AuthError", "BaseError"}


@pytest.mark.parametrize("module_path", sorted(INTEGRATION_ROOT.glob("*.py")))
def test_integration_imports_supported_carrier_api_exception_names(module_path: Path) -> None:
    """Require integration modules to use the supported CarrierApi exception names."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    deprecated_imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "carrier_api":
            deprecated_imports.extend(
                alias.name
                for alias in node.names
                if alias.name in DEPRECATED_CARRIER_API_EXCEPTIONS
            )

    assert deprecated_imports == []


@pytest.mark.parametrize(
    ("classifier", "nested_error"),
    [
        (is_unauthorized_error, CarrierApiAuthError("unauthorized")),
        (is_transient_transport_error, CarrierApiConnectionError("temporary")),
        (is_transient_transport_error, CarrierApiTokenRefreshError("temporary")),
        (is_transient_transport_error, CarrierApiWebsocketError("temporary")),
        (is_transient_transport_error, TimeoutError("temporary")),
        (is_transient_transport_error, OSError("temporary")),
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


def test_graphql_errors_are_not_classified_as_transient_transport() -> None:
    """Keep Carrier GraphQL rejections out of transport retry classification."""
    assert is_transient_transport_error(CarrierApiGraphqlError("rejected")) is False


def test_async_redact_data_recursively_redacts_mappings_and_lists() -> None:
    """Redact sensitive keys while preserving unrelated nested payload data."""
    payload = {"username": "user", "nested": [{"password": "secret", "mode": "auto"}]}

    redacted = async_redact_data(payload, {"username", "password"})

    assert redacted == {
        "username": "**REDACTED**",
        "nested": [{"password": "**REDACTED**", "mode": "auto"}],
    }
    assert payload["username"] == "user"
