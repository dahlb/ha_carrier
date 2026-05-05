"""Pytest workflow tests for the Carrier data update coordinator."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.ha_carrier.carrier_data_update_coordinator import (
    CarrierDataUpdateCoordinator,
)
from custom_components.ha_carrier.const import (
    TRANSIENT_FAILURE_THRESHOLD,
    UNAUTHORIZED_RETRY_THRESHOLD,
)
from custom_components.ha_carrier.exceptions import CarrierUnauthorizedError
from custom_components.ha_carrier.resiliency import ResiliencyState

from .conftest import FakeCarrierApiConnection, build_carrier_system


@pytest.mark.asyncio
async def test_initial_full_refresh_preserves_systems_list_identity(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Mutate the systems list in place when initially loading systems."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    systems: list[Any] = []
    fresh_systems = [build_carrier_system()]
    carrier_api.systems = fresh_systems
    coordinator.systems = systems
    coordinator.api_connection = carrier_api
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
    )
    coordinator._websocket_initialized = True

    await coordinator._async_full_refresh()

    assert coordinator.systems is systems
    assert coordinator.systems == fresh_systems


@pytest.mark.asyncio
async def test_energy_refresh_uses_cycle_scoped_success_reset(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Preserve resiliency state across per-system energy successes."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    coordinator.api_connection = carrier_api
    coordinator.resiliency = cast("Any", SimpleNamespace(reset_unauthorized=lambda: None))
    calls: list[dict[str, Any]] = []

    async def fake_retry(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        """Record retry options and return an energy payload."""
        calls.append(kwargs)
        return {"infinityEnergy": carrier_api.systems[0].energy.raw}

    with patch(
        "custom_components.ha_carrier.carrier_data_update_coordinator.async_call_with_retry",
        fake_retry,
    ):
        await coordinator._async_energy_refresh()

    assert calls[0]["reset_state_on_success"] is False


@pytest.mark.asyncio
async def test_update_attempts_refresh_when_previous_auth_count_is_escalated() -> None:
    """Require a fresh failed read before converting an old auth streak to reauth."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
        consecutive_unauthorized=UNAUTHORIZED_RETRY_THRESHOLD,
    )
    coordinator.data_flush = True
    coordinator.systems = [build_carrier_system()]
    full_refresh_called = False

    async def fake_full_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Simulate a successful full refresh that clears resiliency state."""
        nonlocal full_refresh_called
        full_refresh_called = True
        self.resiliency.reset()
        self.data_flush = False

    with (
        patch.object(CarrierDataUpdateCoordinator, "_async_full_refresh", fake_full_refresh),
        patch.object(
            CarrierDataUpdateCoordinator,
            "mapped_system_data",
            staticmethod(lambda _system: {"serial": "ABC123"}),
        ),
    ):
        data = await coordinator._async_update_data()

    assert full_refresh_called is True
    assert coordinator.resiliency.consecutive_unauthorized == 0
    assert data == [{"serial": "ABC123"}]


@pytest.mark.asyncio
async def test_successful_write_reconciliation_clears_escalated_auth_state() -> None:
    """Let a successful reconciliation refresh clear a write auth streak."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
        consecutive_unauthorized=UNAUTHORIZED_RETRY_THRESHOLD,
    )
    coordinator.data_flush = False
    coordinator.update_interval = None

    async def fake_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Simulate a successful reconciliation refresh."""
        self.resiliency.reset()

    with patch.object(CarrierDataUpdateCoordinator, "async_refresh", fake_refresh):
        await coordinator._async_reconcile_failed_write(
            "test write",
            CarrierUnauthorizedError("unauthorized"),
        )

    assert coordinator.resiliency.consecutive_unauthorized == 0


@pytest.mark.asyncio
async def test_recoverable_write_communication_error_reconciles_and_raises_ha_error() -> None:
    """Surface exhausted write communication failures as Home Assistant errors."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.resiliency = ResiliencyState(unauthorized_threshold=3, transient_threshold=3)
    reconciled = False

    async def request() -> None:
        """Raise a retryable write timeout."""
        raise TimeoutError("timeout")

    async def fake_reconcile(
        self: CarrierDataUpdateCoordinator,
        operation_name: str,
        error: BaseException | None = None,
    ) -> None:
        """Record reconciliation after the failed write."""
        nonlocal reconciled
        assert operation_name == "set mode"
        assert isinstance(error, TimeoutError)
        reconciled = True

    with (
        patch.object(
            CarrierDataUpdateCoordinator,
            "_async_reconcile_failed_write",
            fake_reconcile,
        ),
        pytest.raises(HomeAssistantError, match="timed out"),
    ):
        await coordinator.async_perform_api_call("set mode", request)

    assert reconciled is True
