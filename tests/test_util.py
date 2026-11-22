"""Pytest coverage for Carrier utility helpers."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

from carrier_api import (
    CarrierApiAuthError,
    CarrierApiConnectionError,
    CarrierApiError,
    CarrierApiGraphqlError,
    CarrierApiTokenRefreshError,
    CarrierApiWebsocketError,
)
import pytest

from custom_components.ha_carrier.util import (
    RECOVERABLE_REFRESH_EXCEPTIONS,
    REDACTED,
    TRANSIENT_TRANSPORT_EXCEPTIONS,
    async_get_carrier_identity_id,
    async_redact_data,
    is_retryable_error,
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
    ],
)
def test_exception_classifiers_inspect_unsuppressed_context(
    classifier: Callable[[BaseException], bool],
    nested_error: BaseException,
) -> None:
    """Find classified errors on an implicitly chained context."""
    error = RuntimeError("root")
    error.__context__ = nested_error

    assert classifier(error) is True


@pytest.mark.parametrize(
    ("classifier", "nested_error"),
    [
        (is_unauthorized_error, CarrierApiAuthError("unauthorized")),
        (is_transient_transport_error, CarrierApiConnectionError("temporary")),
        (is_transient_transport_error, CarrierApiTokenRefreshError("temporary")),
        (is_transient_transport_error, CarrierApiWebsocketError("temporary")),
    ],
)
def test_exception_classifiers_ignore_context_suppressed_by_explicit_cause(
    classifier: Callable[[BaseException], bool],
    nested_error: BaseException,
) -> None:
    """Ignore an earlier, already handled error once ``raise ... from`` replaced it."""
    error = RuntimeError("root")
    error.__context__ = nested_error
    # Setting __cause__ suppresses the context, exactly like ``raise ... from``.
    error.__cause__ = ValueError("non-matching cause")

    assert error.__suppress_context__ is True
    assert classifier(error) is False


def test_exception_classifiers_ignore_context_suppressed_by_from_none() -> None:
    """``raise ... from None`` hides a handled 401 from classification."""

    def _raise_unrelated_after_handled_401() -> None:
        """Handle a 401, then raise an unrelated error with its context hidden."""
        try:
            raise CarrierApiAuthError("unauthorized")
        except CarrierApiAuthError:
            raise RuntimeError("unrelated") from None

    with pytest.raises(RuntimeError) as exc_info:
        _raise_unrelated_after_handled_401()

    assert is_unauthorized_error(exc_info.value) is False


def test_graphql_errors_are_not_classified_as_transient_transport() -> None:
    """Keep Carrier GraphQL rejections out of transport retry classification."""
    assert is_transient_transport_error(CarrierApiGraphqlError("rejected")) is False


def test_graphql_errors_are_classified_as_retryable() -> None:
    """Retry a rejected GraphQL operation without calling it a transport error."""
    error = CarrierApiGraphqlError("rejected")

    assert is_retryable_error(error) is True
    assert is_transient_transport_error(error) is False


def test_transport_errors_remain_retryable() -> None:
    """Keep every transport error inside the widened retry classification."""
    assert is_retryable_error(CarrierApiConnectionError("boom")) is True
    assert is_retryable_error(CarrierApiTokenRefreshError("boom")) is True
    assert is_retryable_error(CarrierApiWebsocketError("boom")) is True


def test_auth_errors_are_not_retryable() -> None:
    """Leave 401 handling to the unauthorized branch of the retry helper."""
    assert is_retryable_error(CarrierApiAuthError("denied")) is False


def test_async_redact_data_recursively_redacts_mappings_and_lists() -> None:
    """Redact sensitive keys while preserving unrelated nested payload data."""
    payload = {"username": "user", "nested": [{"password": "secret", "mode": "auto"}]}

    redacted = async_redact_data(payload, {"username", "password"})

    assert redacted == {
        "username": "**REDACTED**",
        "nested": [{"password": "**REDACTED**", "mode": "auto"}],
    }
    assert payload["username"] == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_info",
    [{}, [], {"user": {}}, {"user": "not-a-dict"}, {"user": {"identityId": ""}}],
)
async def test_identity_id_is_none_for_unexpected_user_info(user_info: Any) -> None:
    """Return None instead of raising when Carrier's user info has an unexpected shape."""

    class _Api:
        """Minimal Carrier API stand-in."""

        async def load_data(self) -> list[Any]:
            """Pretend to load systems."""
            return []

        async def get_user_info(self) -> Any:
            """Return the parametrized payload."""
            return user_info

    assert await async_get_carrier_identity_id(_Api()) is None


def test_async_redact_data_passes_scalars_and_top_level_lists_through() -> None:
    """Return scalars unchanged and redact inside a top-level list."""
    assert async_redact_data("serial", {"serial"}) == "serial"
    assert async_redact_data([{"serial": "ABC"}, 1], {"serial"}) == [{"serial": REDACTED}, 1]


def test_exception_classifiers_stop_on_a_cyclic_context_chain() -> None:
    """Visit each exception once when contexts point back at each other."""
    first = RuntimeError("first")
    second = ValueError("second")
    first.__context__ = second
    second.__context__ = first

    assert is_unauthorized_error(first) is False


@pytest.mark.parametrize(
    ("error_type", "transport"),
    [
        (CarrierApiAuthError, False),
        (CarrierApiGraphqlError, False),
        (CarrierApiConnectionError, True),
        (CarrierApiTokenRefreshError, True),
        (CarrierApiWebsocketError, True),
    ],
)
def test_exception_tuples_cover_the_carrier_api_hierarchy(
    error_type: type[CarrierApiError], transport: bool
) -> None:
    """Pin the carrier_api hierarchy the collapsed exception tuples rely on."""
    assert issubclass(error_type, CarrierApiError)
    assert issubclass(error_type, RECOVERABLE_REFRESH_EXCEPTIONS)
    assert issubclass(error_type, TRANSIENT_TRANSPORT_EXCEPTIONS) is transport
