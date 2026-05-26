"""Pytest workflow tests for the Carrier data update coordinator."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from carrier_api import CarrierApiAuthError, CarrierApiGraphqlError
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
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
    coordinator.api_connection = cast("Any", carrier_api)
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
    coordinator.api_connection = cast("Any", carrier_api)
    coordinator.resiliency = cast(
        "Any",
        SimpleNamespace(
            reset_unauthorized=lambda: None,
            reset_transient=lambda: None,
        ),
    )
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


@pytest.mark.asyncio
async def test_carrier_api_write_rejection_reconciles_and_raises_ha_error() -> None:
    """Surface Carrier API business rejections as Home Assistant errors."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.resiliency = ResiliencyState(unauthorized_threshold=3, transient_threshold=3)
    reconciled = False

    async def request() -> None:
        """Raise a Carrier API GraphQL rejection."""
        raise CarrierApiGraphqlError("rejected")

    async def fake_reconcile(
        self: CarrierDataUpdateCoordinator,
        operation_name: str,
        error: BaseException | None = None,
    ) -> None:
        """Record reconciliation after the failed write."""
        nonlocal reconciled
        assert operation_name == "set mode"
        assert isinstance(error, CarrierApiGraphqlError)
        reconciled = True

    with (
        patch.object(
            CarrierDataUpdateCoordinator,
            "_async_reconcile_failed_write",
            fake_reconcile,
        ),
        pytest.raises(HomeAssistantError, match="Carrier rejected the request"),
    ):
        await coordinator.async_perform_api_call("set mode", request)

    assert reconciled is True


@pytest.mark.asyncio
async def test_update_data_translates_unauthorized_refresh_to_reauth() -> None:
    """Escalate a fresh unauthorized refresh failure to HA reauthentication."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.data_flush = True

    async def fake_full_refresh() -> None:
        """Raise an escalated Carrier auth failure."""
        raise CarrierUnauthorizedError("unauthorized")

    with (
        patch.object(coordinator, "_async_full_refresh", fake_full_refresh),
        pytest.raises(ConfigEntryAuthFailed) as exc_info,
    ):
        await coordinator._async_update_data()

    assert exc_info.type.__name__ == "ConfigEntryAuthFailed"
    assert coordinator.data_flush is True


@pytest.mark.asyncio
async def test_update_data_keeps_plain_unauthorized_server_error_retryable() -> None:
    """Keep non-escalated unauthorized transport errors on HA retry cadence."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.data_flush = True
    coordinator.update_interval = None

    async def fake_full_refresh() -> None:
        """Raise a 401 transport response before resiliency escalation."""
        raise CarrierApiAuthError("unauthorized")

    with (
        patch.object(coordinator, "_async_full_refresh", fake_full_refresh),
        pytest.raises(UpdateFailed, match="temporarily rejected"),
    ):
        await coordinator._async_update_data()

    assert coordinator.data_flush is True
    assert coordinator.update_interval is not None


@pytest.mark.asyncio
async def test_full_refresh_merges_new_changed_and_stale_systems(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Merge fresh full-refresh systems in place and remove stale systems."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    existing = build_carrier_system(serial="ABC123", name="Old")
    stale = build_carrier_system(serial="STALE", name="Stale")
    fresh_existing = build_carrier_system(serial="ABC123", name="Updated")
    fresh_new = build_carrier_system(serial="NEW123", name="New")
    carrier_api.systems = [fresh_existing, fresh_new]
    coordinator.systems = [existing, stale]
    coordinator.api_connection = cast("Any", carrier_api)
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
    )
    coordinator._websocket_initialized = False

    await coordinator._async_full_refresh()

    assert coordinator.systems[0] is existing
    assert coordinator.systems[0].profile.name == "Updated"
    assert [system.profile.serial for system in coordinator.systems] == ["ABC123", "NEW123"]
    assert len(carrier_api.api_websocket.callbacks) == 2
    assert coordinator.data_flush is False


@pytest.mark.asyncio
async def test_energy_refresh_records_one_unauthorized_per_cycle(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Preserve prior energy and escalate only one auth count per energy cycle."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    systems = [
        build_carrier_system(serial="ABC123"),
        build_carrier_system(serial="DEF456"),
    ]
    coordinator.systems = systems
    coordinator.api_connection = cast("Any", carrier_api)
    coordinator.resiliency = ResiliencyState(unauthorized_threshold=2, transient_threshold=3)
    coordinator.update_interval = None

    async def fake_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Raise unauthorized for every per-system energy request."""
        raise CarrierApiAuthError("unauthorized")

    with patch(
        "custom_components.ha_carrier.carrier_data_update_coordinator.async_call_with_retry",
        fake_retry,
    ):
        await coordinator._async_energy_refresh()

    assert coordinator.resiliency.consecutive_unauthorized == 1
    assert coordinator.update_interval is not None
    assert systems[0].energy.raw is not None


@pytest.mark.asyncio
async def test_energy_refresh_escalates_after_threshold(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Raise CarrierUnauthorizedError once the energy-cycle auth threshold is crossed."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    coordinator.api_connection = cast("Any", carrier_api)
    coordinator.resiliency = ResiliencyState(unauthorized_threshold=1, transient_threshold=3)
    coordinator.update_interval = None

    async def fake_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Raise unauthorized for the energy request."""
        raise CarrierApiAuthError("unauthorized")

    with (
        patch(
            "custom_components.ha_carrier.carrier_data_update_coordinator.async_call_with_retry",
            fake_retry,
        ),
        pytest.raises(CarrierUnauthorizedError),
    ):
        await coordinator._async_energy_refresh()


@pytest.mark.asyncio
async def test_updated_callback_records_timestamp_and_notifies_listeners() -> None:
    """Handle websocket callbacks by timestamping and notifying listeners."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    notified = False

    def fake_update_listeners() -> None:
        """Record listener notification."""
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_update_listeners):
        await coordinator.updated_callback("{}")

    assert coordinator.timestamp_websocket is not None
    assert notified is True


def test_system_returns_matching_system_or_none() -> None:
    """Look up tracked systems by Carrier serial."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    system = build_carrier_system(serial="ABC123")
    coordinator.systems = [system]

    assert coordinator.system("ABC123") is system
    assert coordinator.system("missing") is None
