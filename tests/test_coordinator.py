"""Pytest workflow tests for the Carrier data update coordinator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from carrier_api import (
    ActivityTypes,
    CarrierApiAuthError,
    CarrierApiConnectionError,
    CarrierApiGraphqlError,
)
from carrier_api.api_websocket_data_updater import WebsocketDataUpdater
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.ha_carrier.const import (
    DEFAULT_FULL_REFRESH_INTERVAL_MINUTES,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    TRANSIENT_FAILURE_THRESHOLD,
    UNAUTHORIZED_RETRY_THRESHOLD,
)
from custom_components.ha_carrier.coordinator import CarrierDataUpdateCoordinator, poll_interval_for
from custom_components.ha_carrier.exceptions import CarrierUnauthorizedError
from custom_components.ha_carrier.resiliency import ResiliencyState

from .conftest import FakeCarrierApiConnection, build_carrier_system


def _set_coordinator_api_connection(
    coordinator: CarrierDataUpdateCoordinator,
    api_connection: FakeCarrierApiConnection,
) -> None:
    """Attach a fake API connection to a partially constructed coordinator."""
    object.__setattr__(coordinator, "api_connection", api_connection)


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
    _set_coordinator_api_connection(coordinator, carrier_api)
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
    _set_coordinator_api_connection(coordinator, carrier_api)
    object.__setattr__(
        coordinator,
        "resiliency",
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
        "custom_components.ha_carrier.coordinator.async_call_with_retry",
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
        """Raise a retryable write communication failure."""
        raise CarrierApiConnectionError("timeout")

    async def fake_reconcile(
        self: CarrierDataUpdateCoordinator,
        operation_name: str,
        error: BaseException | None = None,
    ) -> None:
        """Record reconciliation after the failed write."""
        nonlocal reconciled
        assert operation_name == "set mode"
        assert isinstance(error, CarrierApiConnectionError)
        reconciled = True

    with (
        patch.object(
            CarrierDataUpdateCoordinator,
            "_async_reconcile_failed_write",
            fake_reconcile,
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await coordinator.async_perform_api_call("set mode", request)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "communication_error"

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
        pytest.raises(HomeAssistantError) as raised,
    ):
        await coordinator.async_perform_api_call("set mode", request)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "request_rejected"

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
    _set_coordinator_api_connection(coordinator, carrier_api)
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
    )
    coordinator._websocket_initialized = False

    await coordinator._async_full_refresh()

    assert coordinator.systems[0] is existing
    assert coordinator.systems[0].profile.name == "Updated"
    assert [system.profile.serial for system in coordinator.systems] == ["ABC123", "NEW123"]
    assert carrier_api.api_websocket.callbacks == [coordinator.updated_callback]
    assert coordinator.websocket_data_updater is not None
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
    existing_energy = systems[0].energy
    coordinator.systems = systems
    _set_coordinator_api_connection(coordinator, carrier_api)
    coordinator.resiliency = ResiliencyState(unauthorized_threshold=2, transient_threshold=3)
    coordinator.update_interval = None

    async def fake_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Raise unauthorized for every per-system energy request."""
        raise CarrierApiAuthError("unauthorized")

    with patch(
        "custom_components.ha_carrier.coordinator.async_call_with_retry",
        fake_retry,
    ):
        await coordinator._async_energy_refresh()

    assert coordinator.resiliency.consecutive_unauthorized == 1
    assert coordinator.update_interval is not None
    assert systems[0].energy is existing_energy


@pytest.mark.asyncio
async def test_update_data_forces_full_refresh_when_reconcile_interval_elapsed() -> None:
    """Force a full reconcile when websocket-maintained data is overdue for a full fetch."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.data_flush = False
    coordinator._intercept_guards = {}
    coordinator.systems = [build_carrier_system()]
    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=DEFAULT_FULL_REFRESH_INTERVAL_MINUTES + 1
    )
    full_refresh_called = False
    energy_refresh_called = False

    async def fake_full_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Record that the periodic poll performed a full reconcile."""
        nonlocal full_refresh_called
        full_refresh_called = True
        self.data_flush = False

    async def fake_energy_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Record that the periodic poll performed only an energy refresh."""
        nonlocal energy_refresh_called
        energy_refresh_called = True

    with (
        patch.object(CarrierDataUpdateCoordinator, "_async_full_refresh", fake_full_refresh),
        patch.object(CarrierDataUpdateCoordinator, "_async_energy_refresh", fake_energy_refresh),
        patch.object(
            CarrierDataUpdateCoordinator,
            "mapped_system_data",
            staticmethod(lambda _system: {"serial": "ABC123"}),
        ),
    ):
        await coordinator._async_update_data()

    assert full_refresh_called is True
    assert energy_refresh_called is False


@pytest.mark.asyncio
async def test_update_data_stays_energy_only_before_reconcile_interval() -> None:
    """Keep the periodic poll energy-only until the full-reconcile interval elapses."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.data_flush = False
    coordinator.systems = [build_carrier_system()]
    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=DEFAULT_FULL_REFRESH_INTERVAL_MINUTES - DEFAULT_UPDATE_INTERVAL_MINUTES
    )
    full_refresh_called = False
    energy_refresh_called = False

    async def fake_full_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Record an unexpected full refresh."""
        nonlocal full_refresh_called
        full_refresh_called = True

    async def fake_energy_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Record the expected energy-only refresh."""
        nonlocal energy_refresh_called
        energy_refresh_called = True

    with (
        patch.object(CarrierDataUpdateCoordinator, "_async_full_refresh", fake_full_refresh),
        patch.object(CarrierDataUpdateCoordinator, "_async_energy_refresh", fake_energy_refresh),
        patch.object(
            CarrierDataUpdateCoordinator,
            "mapped_system_data",
            staticmethod(lambda _system: {"serial": "ABC123"}),
        ),
    ):
        await coordinator._async_update_data()

    assert energy_refresh_called is True
    assert full_refresh_called is False


def test_full_reconcile_due_covers_timestamp_states() -> None:
    """Report reconcile due when never refreshed or overdue, and not when fresh."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)

    coordinator.timestamp_all_data = None
    assert coordinator._full_reconcile_due() is True

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=DEFAULT_FULL_REFRESH_INTERVAL_MINUTES + 1
    )
    assert coordinator._full_reconcile_due() is True

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=DEFAULT_FULL_REFRESH_INTERVAL_MINUTES - DEFAULT_UPDATE_INTERVAL_MINUTES
    )
    assert coordinator._full_reconcile_due() is False


@pytest.mark.parametrize(
    ("full_refresh_minutes", "poll_minutes"),
    [(120, 30), (90, 30), (60, 30), (100, 25), (45, 22.5), (30, 30), (10, 10), (5, 5)],
)
def test_poll_interval_divides_full_refresh_interval_evenly(
    full_refresh_minutes: int, poll_minutes: float
) -> None:
    """Pick the longest poll interval, at most the default, that divides the full interval."""
    assert poll_interval_for(timedelta(minutes=full_refresh_minutes)) == timedelta(
        minutes=poll_minutes
    )


def test_full_reconcile_due_honours_a_configured_interval() -> None:
    """Treat a full refresh as due from half a poll interval before the configured interval."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.full_refresh_interval = timedelta(minutes=5)
    coordinator.poll_interval = timedelta(minutes=5)

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(minutes=5)
    assert coordinator._full_reconcile_due() is True

    # A poll fired a few seconds early, or an off-cycle refresh close to due.
    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(minutes=4, seconds=55)
    assert coordinator._full_reconcile_due() is True
    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(minutes=3)
    assert coordinator._full_reconcile_due() is True

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(minutes=2)
    assert coordinator._full_reconcile_due() is False


def test_full_reconcile_due_keeps_the_default_cadence() -> None:
    """Keep the default full refresh on the fourth 30-minute poll, not the third."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(minutes=90)
    assert coordinator._full_reconcile_due() is False

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(minutes=119, seconds=59)
    assert coordinator._full_reconcile_due() is True


@pytest.mark.asyncio
async def test_update_data_defers_scheduled_full_refresh_during_post_write_window() -> None:
    """Keep a due full refresh from ending a live post-write guard."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.data_flush = False
    coordinator.systems = [build_carrier_system()]
    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=DEFAULT_FULL_REFRESH_INTERVAL_MINUTES + 1
    )
    coordinator._intercept_guards = {
        ("ABC123", None): {
            "expires_at": datetime.now(UTC) + timedelta(minutes=2),
            "mode": "cool",
        }
    }
    calls: list[str] = []

    async def fake_full_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Record a full refresh."""
        calls.append("full")

    async def fake_energy_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Record an energy-only refresh."""
        calls.append("energy")

    with (
        patch.object(CarrierDataUpdateCoordinator, "_async_full_refresh", fake_full_refresh),
        patch.object(CarrierDataUpdateCoordinator, "_async_energy_refresh", fake_energy_refresh),
    ):
        await coordinator._async_update_data()
        coordinator._intercept_guards = {}
        await coordinator._async_update_data()

    assert calls == ["energy", "full"]


@pytest.mark.asyncio
async def test_coordinator_polls_on_the_configured_full_refresh_cadence(
    hass: HomeAssistant,
) -> None:
    """Shorten the poll interval to fit a low full refresh interval."""
    coordinator = CarrierDataUpdateCoordinator(
        hass,
        FakeCarrierApiConnection(),
        full_refresh_interval=timedelta(minutes=5),
    )

    assert coordinator.full_refresh_interval == timedelta(minutes=5)
    assert coordinator.poll_interval == timedelta(minutes=5)
    assert coordinator.update_interval == timedelta(minutes=5)


@pytest.mark.asyncio
async def test_begin_post_write_intercept_captures_written_target_only() -> None:
    """Opening a guard records the written system's mode and that zone's set points."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    system = build_carrier_system()
    coordinator.systems = [system]
    coordinator._intercept_guards = {}
    status_zone = system.status.zones[0]
    resolved = system.config.zones[0].current_status_activity(status_zone) or status_zone

    coordinator.begin_post_write_intercept(system.profile.serial, status_zone.api_id)

    mode_guard = coordinator._intercept_guards[(system.profile.serial, None)]
    assert mode_guard["expires_at"] > datetime.now(UTC)
    assert mode_guard["mode"] == system.config.mode
    zone_guard = coordinator._intercept_guards[(system.profile.serial, status_zone.api_id)]
    assert zone_guard["expires_at"] > datetime.now(UTC)
    assert zone_guard["activity_type"] == status_zone.current_status_activity_type
    assert zone_guard["cool_set_point"] == resolved.cool_set_point
    assert zone_guard["heat_set_point"] == resolved.heat_set_point


@pytest.mark.asyncio
async def test_reasserts_reverted_mode_and_still_publishes() -> None:
    """A reverted system mode is restored, and the message is still published."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system = build_carrier_system()
    coordinator.systems = [system]
    coordinator._intercept_guards = {}
    original_mode = system.config.mode
    coordinator.begin_post_write_intercept(system.profile.serial, system.status.zones[0].api_id)

    # Stale replay reverts the mode.
    system.config.mode = "heat" if original_mode != "heat" else "cool"

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("stale replay reverts mode")

    assert system.config.mode == original_mode
    assert notified is True


@pytest.mark.asyncio
async def test_system_level_write_reasserts_mode_without_zone() -> None:
    """A system-level write (zone_api_id=None) protects mode and records no zone."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system = build_carrier_system()
    coordinator.systems = [system]
    coordinator._intercept_guards = {}
    original_mode = system.config.mode

    coordinator.begin_post_write_intercept(system.profile.serial, None)

    assert (system.profile.serial, None) in coordinator._intercept_guards
    # No zone guard is recorded for a system-level write.
    assert all(zone is None for _serial, zone in coordinator._intercept_guards)

    # Stale replay reverts the system mode.
    system.config.mode = "heat" if original_mode != "heat" else "cool"

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("stale replay reverts mode")

    assert system.config.mode == original_mode
    assert notified is True


@pytest.mark.asyncio
async def test_reasserts_reverted_setpoint_and_still_publishes() -> None:
    """A reverted set point is restored to the written value; message still published."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system = build_carrier_system()
    coordinator.systems = [system]
    coordinator._intercept_guards = {}
    status_zone = system.status.zones[0]
    activity = system.config.zones[0].find_activity(status_zone.current_status_activity_type)
    assert activity is not None
    commanded_cool = activity.cool_set_point
    coordinator.begin_post_write_intercept(system.profile.serial, status_zone.api_id)

    # Stale replay reverts the resolved set point.
    activity.cool_set_point = commanded_cool + 3
    status_zone.cool_set_point = commanded_cool + 3

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("stale replay reverts setpoint")

    resolved = system.config.zones[0].current_status_activity(status_zone)
    assert resolved is not None
    assert resolved.cool_set_point == commanded_cool
    assert status_zone.cool_set_point == commanded_cool
    assert notified is True


@pytest.mark.asyncio
async def test_matching_state_is_not_rewritten() -> None:
    """When nothing reverted, re-assert leaves state as-is and still publishes."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system = build_carrier_system()
    coordinator.systems = [system]
    coordinator._intercept_guards = {}
    coordinator.begin_post_write_intercept(system.profile.serial, system.status.zones[0].api_id)
    before_mode = system.config.mode

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("benign reading update")

    assert system.config.mode == before_mode
    assert notified is True


@pytest.mark.asyncio
async def test_control_revert_does_not_drop_other_system_update() -> None:
    """A reverted control field on the written system must not drop another system's update.

    One Carrier websocket message carries every system/zone, applied by
    message_handler before updated_callback runs. Re-asserting only the written
    system's reverted field (instead of suppressing the whole notification) keeps
    the other system's already-applied change (e.g. going idle) reaching Home
    Assistant.
    """
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system_a = build_carrier_system(serial="A", zone_id="1")
    system_b = build_carrier_system(serial="B", zone_id="2")
    coordinator.systems = [system_a, system_b]
    coordinator._intercept_guards = {}
    # Only system A was written.
    coordinator.begin_post_write_intercept(system_a.profile.serial, system_a.status.zones[0].api_id)
    a_mode = system_a.config.mode

    # One websocket message: system A's mode reverted AND system B goes idle.
    system_a.config.mode = "heat" if a_mode != "heat" else "cool"
    system_b.status.zones[0].conditioning = "idle"

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("reverts A, idles B")

    assert notified is True, "system B's idle update must still reach HA"
    assert system_a.config.mode == a_mode
    assert system_b.status.zones[0].conditioning == "idle"


@pytest.mark.asyncio
async def test_legit_change_on_unwritten_system_survives() -> None:
    """A legitimate control change on an unwritten system during the window is not reverted.

    Windows are scoped to written systems only, so a real mode change on any other
    system during one — a second thermostat, or this integration's own
    ``climate.turn_off`` auto-shutoff writing a different system — flows through
    untouched instead of being reverted to the written system's stale snapshot.
    """
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system_a = build_carrier_system(serial="A", zone_id="1")
    system_b = build_carrier_system(serial="B", zone_id="2")
    coordinator.systems = [system_a, system_b]
    coordinator._intercept_guards = {}
    # Only system A was written.
    coordinator.begin_post_write_intercept(system_a.profile.serial, system_a.status.zones[0].api_id)
    a_mode = system_a.config.mode
    b_new_mode = "off" if system_b.config.mode != "off" else "cool"

    # One websocket message: A's mode reverts (stale replay) AND B legitimately changes.
    system_a.config.mode = "heat" if a_mode != "heat" else "cool"
    system_b.config.mode = b_new_mode

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("reverts A, legit change on B")

    assert system_a.config.mode == a_mode, "written system A's reverted mode is restored"
    assert system_b.config.mode == b_new_mode, "unwritten system B's legit change survives"
    assert notified is True


@pytest.mark.asyncio
async def test_overlapping_windows_protect_each_written_target() -> None:
    """A second write to a different target must not drop the first target's protection.

    Windows are tracked per written system, so two writes to different systems
    within their windows each keep their own protection. This is the singleton-vs-
    collection regression: a coordinator-wide single window would let the second
    write overwrite the first, silently abandoning the first target's revert guard.
    """
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system_a = build_carrier_system(serial="A", zone_id="1")
    system_b = build_carrier_system(serial="B", zone_id="2")
    coordinator.systems = [system_a, system_b]
    coordinator._intercept_guards = {}
    a_zone = system_a.status.zones[0]
    b_zone = system_b.status.zones[0]
    a_activity = system_a.config.zones[0].find_activity(a_zone.current_status_activity_type)
    b_activity = system_b.config.zones[0].find_activity(b_zone.current_status_activity_type)
    assert a_activity is not None and b_activity is not None
    a_cool = a_activity.cool_set_point
    b_cool = b_activity.cool_set_point

    # Two writes to different systems, both still within their windows.
    coordinator.begin_post_write_intercept(system_a.profile.serial, a_zone.api_id)
    coordinator.begin_post_write_intercept(system_b.profile.serial, b_zone.api_id)

    # One websocket message replays BOTH systems' pre-write set points.
    a_activity.cool_set_point = a_cool + 3
    a_zone.cool_set_point = a_cool + 3
    b_activity.cool_set_point = b_cool + 3
    b_zone.cool_set_point = b_cool + 3

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("reverts both A and B")

    a_resolved = system_a.config.zones[0].current_status_activity(a_zone)
    b_resolved = system_b.config.zones[0].current_status_activity(b_zone)
    assert a_resolved is not None and b_resolved is not None
    assert a_resolved.cool_set_point == a_cool, "first write's revert must be restored"
    assert b_resolved.cool_set_point == b_cool, "second write's revert must be restored"
    assert notified is True


@pytest.mark.asyncio
async def test_updated_callback_notifies_when_not_in_window() -> None:
    """Outside a post-write window, updates publish normally without re-assert."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    coordinator.systems = [build_carrier_system()]
    coordinator._intercept_guards = {}

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("normal update")

    assert notified is True


@pytest.mark.asyncio
async def test_expired_guard_is_pruned_and_not_reasserted() -> None:
    """A guard past its expiry is dropped and no longer re-asserts a revert."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system = build_carrier_system()
    coordinator.systems = [system]
    original_mode = system.config.mode
    coordinator._intercept_guards = {
        (system.profile.serial, None): {
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
            "mode": original_mode,
        }
    }

    # Stale replay reverts the mode after the guard has already expired.
    system.config.mode = "heat" if original_mode != "heat" else "cool"
    reverted_mode = system.config.mode

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("post-expiry update")

    assert system.config.mode == reverted_mode, "expired guard must not re-assert"
    assert coordinator._intercept_guards == {}
    assert notified is True


@pytest.mark.asyncio
async def test_same_system_zone_guard_expiry_is_independent() -> None:
    """A written zone's guard expires on its own clock, not extended by a sibling write.

    Two zones of the SAME system are written a few minutes apart. The first zone's
    guard reaching its own expiry must not be kept alive by the second zone's later
    write — otherwise a legitimate later change to the first zone would be clobbered
    as if it were a stale replay. This is the per-entry-expiry regression: a shared
    per-system clock would let the sibling write extend the first zone's guard.
    """
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    system = build_carrier_system(serial="A", zone_id="1", second_zone_id="2")
    coordinator.systems = [system]
    coordinator._intercept_guards = {}
    zone1 = next(zone for zone in system.status.zones if zone.api_id == "1")
    zone2 = next(zone for zone in system.status.zones if zone.api_id == "2")
    cfg1 = next(zone for zone in system.config.zones if zone.api_id == "1")
    cfg2 = next(zone for zone in system.config.zones if zone.api_id == "2")
    act1 = cfg1.find_activity(zone1.current_status_activity_type)
    act2 = cfg2.find_activity(zone2.current_status_activity_type)
    assert act1 is not None and act2 is not None
    zone2_cool = act2.cool_set_point

    coordinator.begin_post_write_intercept("A", "1")
    coordinator.begin_post_write_intercept("A", "2")
    # Zone 1's own five minutes have elapsed while zone 2's guard is still live.
    coordinator._intercept_guards[("A", "1")]["expires_at"] = datetime.now(UTC) - timedelta(
        seconds=1
    )

    # One websocket message: zone 1 gets a legitimate NEW value; zone 2 is stale-reverted.
    zone1_new_cool = act1.cool_set_point + 5
    act1.cool_set_point = zone1_new_cool
    zone1.cool_set_point = zone1_new_cool
    act2.cool_set_point = zone2_cool + 3
    zone2.cool_set_point = zone2_cool + 3

    notified = False

    def fake_notify() -> None:
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_notify):
        await coordinator.updated_callback("zone1 legit change, zone2 revert")

    resolved1 = cfg1.current_status_activity(zone1)
    resolved2 = cfg2.current_status_activity(zone2)
    assert resolved1 is not None and resolved2 is not None
    assert resolved1.cool_set_point == zone1_new_cool, "expired zone-1 guard must not clobber"
    assert resolved2.cool_set_point == zone2_cool, "live zone-2 guard still restores its revert"
    assert ("A", "1") not in coordinator._intercept_guards
    assert notified is True


@pytest.mark.asyncio
async def test_full_refresh_ends_post_write_window(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """An authoritative full read ends every post-write guard."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    _set_coordinator_api_connection(coordinator, carrier_api)
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
    )
    coordinator._websocket_initialized = True
    coordinator._intercept_guards = {
        ("ABC123", None): {
            "expires_at": datetime.now(UTC) + timedelta(minutes=1),
            "mode": "cool",
        }
    }

    await coordinator._async_full_refresh()

    assert coordinator._intercept_guards == {}


@pytest.mark.asyncio
async def test_failed_write_does_not_begin_intercept() -> None:
    """A failed write must not open a post-write intercept window."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
    )
    coordinator._intercept_guards = {}

    async def request() -> None:
        """Raise a retryable write communication failure."""
        raise CarrierApiConnectionError("timeout")

    async def fake_reconcile(
        self: CarrierDataUpdateCoordinator,
        operation_name: str,
        error: BaseException | None = None,
    ) -> None:
        """Swallow the reconcile so the error path can finish."""
        return

    with (
        patch.object(CarrierDataUpdateCoordinator, "_async_reconcile_failed_write", fake_reconcile),
        pytest.raises(HomeAssistantError),
    ):
        await coordinator.async_perform_api_call("set mode", request)

    assert coordinator._intercept_guards == {}


@pytest.mark.asyncio
async def test_energy_refresh_escalates_after_threshold(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Raise CarrierUnauthorizedError once the energy-cycle auth threshold is crossed."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    _set_coordinator_api_connection(coordinator, carrier_api)
    coordinator.resiliency = ResiliencyState(unauthorized_threshold=1, transient_threshold=3)
    coordinator.update_interval = None

    async def fake_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Raise unauthorized for the energy request."""
        raise CarrierApiAuthError("unauthorized")

    with (
        patch(
            "custom_components.ha_carrier.coordinator.async_call_with_retry",
            fake_retry,
        ),
        pytest.raises(CarrierUnauthorizedError),
    ):
        await coordinator._async_energy_refresh()


@pytest.mark.asyncio
async def test_updated_callback_records_timestamp_and_notifies_listeners() -> None:
    """Handle websocket callbacks by timestamping and notifying listeners."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    coordinator.systems = [build_carrier_system()]
    coordinator._intercept_guards = {}
    notified = False

    def fake_update_listeners() -> None:
        """Record listener notification."""
        nonlocal notified
        notified = True

    with patch.object(coordinator, "async_update_listeners", fake_update_listeners):
        await coordinator.updated_callback(
            '{"deviceId": "ABC123", "payload": {"messageType": "InfinityStatus", "oat": 70}}'
        )

    assert coordinator.timestamp_websocket is not None
    assert notified is True


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["{}", "[]", "not json", '{"payload": {"oat": 70}}'])
async def test_updated_callback_does_not_timestamp_messages_without_a_device(
    message: str,
) -> None:
    """Leave the websocket timestamp alone for keepalive replies and other non-device messages."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.websocket_data_updater = None
    coordinator.systems = [build_carrier_system()]
    coordinator._intercept_guards = {}
    coordinator.timestamp_websocket = None

    with patch.object(coordinator, "async_update_listeners", lambda: None):
        await coordinator.updated_callback(message)

    assert coordinator.timestamp_websocket is None


@pytest.mark.asyncio
async def test_updated_callback_applies_message_with_websocket_data_updater() -> None:
    """Hand each websocket message to the data updater before notifying listeners."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    coordinator._intercept_guards = {}
    coordinator.data_flush = False
    handled: list[str] = []

    async def fake_message_handler(message: str) -> None:
        """Record the message handed to the data updater."""
        handled.append(message)

    coordinator.websocket_data_updater = SimpleNamespace(  # type: ignore[assignment]
        message_handler=fake_message_handler
    )

    with patch.object(coordinator, "async_update_listeners", lambda: None):
        await coordinator.updated_callback('{"deviceId": "ABC123"}')

    assert handled == ['{"deviceId": "ABC123"}']
    assert coordinator.data_flush is False


@pytest.mark.asyncio
async def test_updated_callback_flushes_data_on_partial_message() -> None:
    """Request a full refresh when a websocket message is not valid JSON."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    coordinator._intercept_guards = {}
    coordinator.data_flush = False
    coordinator.websocket_data_updater = WebsocketDataUpdater(systems=coordinator.systems)
    refresh_requests = 0
    notified = False

    async def fake_request_refresh() -> None:
        """Record the refresh request."""
        nonlocal refresh_requests
        refresh_requests += 1

    def fake_update_listeners() -> None:
        """Record listener notification."""
        nonlocal notified
        notified = True

    with (
        patch.object(coordinator, "async_request_refresh", fake_request_refresh),
        patch.object(coordinator, "async_update_listeners", fake_update_listeners),
    ):
        await coordinator.updated_callback('{"deviceId": "ABC123", "payload": {')

    assert coordinator.data_flush is True
    assert refresh_requests == 1
    assert notified is True


def test_system_returns_matching_system_or_none() -> None:
    """Look up tracked systems by Carrier serial."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    system = build_carrier_system(serial="ABC123")
    coordinator.systems = [system]

    assert coordinator.system("ABC123") is system
    assert coordinator.system("missing") is None


def _bare_coordinator(
    carrier_api: FakeCarrierApiConnection | None = None,
) -> CarrierDataUpdateCoordinator:
    """Return a partially built coordinator holding one fake system.

    Args:
        carrier_api: Optional fake API connection to attach.

    Returns:
        CarrierDataUpdateCoordinator: Coordinator without Home Assistant wiring.
    """
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [build_carrier_system()]
    coordinator.entry_level_systems = []
    coordinator._intercept_guards = {}
    coordinator.websocket_data_updater = None
    coordinator.resiliency = ResiliencyState(
        unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
        transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
    )
    if carrier_api is not None:
        _set_coordinator_api_connection(coordinator, carrier_api)
    return coordinator


def test_post_write_guard_ignores_unknown_systems_and_zones() -> None:
    """Open no guard for an unknown system, and only the mode guard for an unknown zone."""
    coordinator = _bare_coordinator()

    coordinator.begin_post_write_intercept("MISSING", "1")
    assert coordinator._intercept_guards == {}

    coordinator.begin_post_write_intercept("ABC123", "99")
    assert list(coordinator._intercept_guards) == [("ABC123", None)]


def test_reassert_skips_targets_that_disappeared() -> None:
    """Skip guards whose system or zone is gone instead of failing."""
    coordinator = _bare_coordinator()
    coordinator.begin_post_write_intercept("ABC123", "1")
    system = coordinator.systems[0]

    system.status.zones.clear()
    coordinator._reassert_control()

    coordinator.systems.clear()
    coordinator._reassert_control()

    assert ("ABC123", "1") in coordinator._intercept_guards


def test_reassert_restores_status_when_the_activity_has_no_config_entry() -> None:
    """Restore the status zone even if the guarded activity is not in the zone config."""
    coordinator = _bare_coordinator()
    coordinator.begin_post_write_intercept("ABC123", "1")
    guard = coordinator._intercept_guards[("ABC123", "1")]
    guard["activity_type"] = ActivityTypes.WAKE
    status_zone = coordinator.systems[0].status.zones[0]

    coordinator._reassert_control()

    assert status_zone.current_status_activity_type == ActivityTypes.WAKE
    assert status_zone.heat_set_point == guard["heat_set_point"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(), KeyboardInterrupt()])
async def test_update_data_and_writes_propagate_cancellation(error: BaseException) -> None:
    """Never swallow cancellation or interpreter shutdown."""
    coordinator = _bare_coordinator()
    coordinator.data_flush = True

    async def raise_error() -> None:
        """Raise the parametrized error."""
        raise error

    with (
        patch.object(coordinator, "_async_full_refresh", raise_error),
        pytest.raises(type(error)),
    ):
        await coordinator._async_update_data()
    with pytest.raises(type(error)):
        await coordinator.async_perform_api_call("set mode", raise_error)


@pytest.mark.asyncio
async def test_full_refresh_requires_a_websocket_client(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Fail clearly when the Carrier client has no websocket to subscribe to."""
    coordinator = _bare_coordinator(carrier_api)
    coordinator._websocket_initialized = False
    carrier_api.api_websocket = None

    with pytest.raises(RuntimeError, match="websocket client is not initialized"):
        await coordinator._async_full_refresh()


@pytest.mark.asyncio
async def test_debug_logging_dumps_redacted_systems(
    carrier_api: FakeCarrierApiConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log each system, with serials redacted, when debug logging is enabled."""
    coordinator = _bare_coordinator(carrier_api)
    coordinator._websocket_initialized = True
    caplog.set_level(logging.DEBUG, logger="custom_components.ha_carrier.coordinator")

    await coordinator._async_full_refresh()
    with patch.object(coordinator, "async_update_listeners", lambda: None):
        await coordinator.updated_callback("{}")

    assert "ABC123" not in caplog.text
    assert "REDACTED" in caplog.text


@pytest.mark.asyncio
async def test_energy_refresh_reraises_non_auth_failures(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Let transient energy failures escape so the refresh reports them."""
    coordinator = _bare_coordinator(carrier_api)

    async def fake_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Raise a connection failure."""
        raise CarrierApiConnectionError("offline")

    with (
        patch("custom_components.ha_carrier.coordinator.async_call_with_retry", fake_retry),
        pytest.raises(CarrierApiConnectionError),
    ):
        await coordinator._async_energy_refresh()


@pytest.mark.asyncio
async def test_write_rejected_for_auth_reconciles_and_raises_translated_error() -> None:
    """Reconcile after repeated 401s on a write, then raise a retry-later error."""
    coordinator = _bare_coordinator()
    reconciled: list[str] = []

    async def fake_retry(*_args: Any, **_kwargs: Any) -> None:
        """Raise an escalated unauthorized error."""
        raise CarrierUnauthorizedError("unauthorized")

    async def fake_reconcile(operation_name: str, error: BaseException | None = None) -> None:
        """Record the reconciliation."""
        reconciled.append(operation_name)

    with (
        patch("custom_components.ha_carrier.coordinator.async_call_with_retry", fake_retry),
        patch.object(coordinator, "_async_reconcile_failed_write", fake_reconcile),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await coordinator.async_perform_api_call("set mode", lambda: None)

    assert raised.value.translation_key == "write_rejected_repeatedly"
    assert reconciled == ["set mode"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expect_flush"),
    [
        (None, False),
        (CarrierApiGraphqlError("bad request"), False),
        (CarrierApiConnectionError("timeout"), True),
    ],
)
async def test_reconcile_failed_write_swallows_refresh_failures(
    error: BaseException | None,
    expect_flush: bool,
) -> None:
    """Only force a full read for transport or auth failures, and never raise."""
    coordinator = _bare_coordinator()
    coordinator.data_flush = False

    async def failing_refresh() -> None:
        """Fail the reconciliation refresh."""
        raise UpdateFailed("still down")

    with patch.object(coordinator, "async_refresh", failing_refresh):
        await coordinator._async_reconcile_failed_write("set mode", error)

    assert coordinator.data_flush is expect_flush


@pytest.mark.asyncio
async def test_entry_level_refresh_failure_is_non_fatal(
    carrier_api: FakeCarrierApiConnection,
) -> None:
    """Keep the previous entry-level systems when their refresh fails."""
    coordinator = _bare_coordinator(carrier_api)
    previous = [SimpleNamespace(serial="EL1")]
    coordinator.entry_level_systems = previous

    async def failing_load() -> list[Any]:
        """Fail the entry-level load."""
        raise CarrierApiConnectionError("offline")

    carrier_api.load_entry_level_data = failing_load
    await coordinator._refresh_entry_level_systems()
    assert coordinator.entry_level_systems is previous

    async def cancelled_load() -> list[Any]:
        """Cancel the entry-level load."""
        raise asyncio.CancelledError

    carrier_api.load_entry_level_data = cancelled_load
    with pytest.raises(asyncio.CancelledError):
        await coordinator._refresh_entry_level_systems()


def test_entry_level_system_lookup_skips_other_serials() -> None:
    """Find an entry-level system by serial among several."""
    coordinator = _bare_coordinator()
    wanted = SimpleNamespace(serial="EL2")
    coordinator.entry_level_systems = [SimpleNamespace(serial="EL1"), wanted]

    assert coordinator.entry_level_system("EL2") is wanted
    assert coordinator.entry_level_system("EL3") is None


def test_mapped_system_data_rejects_non_mapping_payloads() -> None:
    """Raise if the carrier_api serializer stops returning a mapping."""
    system = SimpleNamespace(as_dict=lambda: ["not", "a", "mapping"])

    with pytest.raises(TypeError, match="non-mapping payload"):
        CarrierDataUpdateCoordinator.mapped_system_data(system)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("immediate", "expected_call"),
    [(True, "async_refresh"), (False, "async_request_refresh")],
)
async def test_request_full_refresh_marks_data_dirty_and_refreshes(
    hass: HomeAssistant,
    immediate: bool,
    expected_call: str,
) -> None:
    """Flag a full read, then refresh now or through the debouncer."""
    coordinator = CarrierDataUpdateCoordinator(hass, FakeCarrierApiConnection())
    coordinator.data_flush = False
    calls: list[str] = []

    async def _record_refresh() -> None:
        """Record a direct refresh."""
        calls.append("async_refresh")

    async def _record_request_refresh() -> None:
        """Record a debounced refresh request."""
        calls.append("async_request_refresh")

    with (
        patch.object(coordinator, "async_refresh", _record_refresh),
        patch.object(coordinator, "async_request_refresh", _record_request_refresh),
    ):
        await coordinator.async_request_full_refresh(immediate=immediate)

    assert coordinator.data_flush is True
    assert calls == [expected_call]
