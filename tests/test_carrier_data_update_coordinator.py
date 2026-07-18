"""Pytest workflow tests for the Carrier data update coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from carrier_api import CarrierApiAuthError, CarrierApiConnectionError, CarrierApiGraphqlError
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.ha_carrier.carrier_data_update_coordinator import (
    CarrierDataUpdateCoordinator,
)
from custom_components.ha_carrier.const import (
    FULL_RECONCILE_INTERVAL_MINUTES,
    TRANSIENT_FAILURE_THRESHOLD,
    UNAUTHORIZED_RETRY_THRESHOLD,
)
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
        pytest.raises(HomeAssistantError, match="Failed to communicate"),
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
    existing_energy = systems[0].energy
    coordinator.systems = systems
    _set_coordinator_api_connection(coordinator, carrier_api)
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
    assert systems[0].energy is existing_energy


@pytest.mark.asyncio
async def test_update_data_forces_full_refresh_when_reconcile_interval_elapsed() -> None:
    """Force a full reconcile when websocket-maintained data is overdue for a full fetch."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.data_flush = False
    coordinator.systems = [build_carrier_system()]
    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=FULL_RECONCILE_INTERVAL_MINUTES + 1
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
        minutes=FULL_RECONCILE_INTERVAL_MINUTES - 1
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
        minutes=FULL_RECONCILE_INTERVAL_MINUTES + 1
    )
    assert coordinator._full_reconcile_due() is True

    coordinator.timestamp_all_data = datetime.now(UTC) - timedelta(
        minutes=FULL_RECONCILE_INTERVAL_MINUTES - 1
    )
    assert coordinator._full_reconcile_due() is False


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
    coordinator._intercept_guards = {}
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
