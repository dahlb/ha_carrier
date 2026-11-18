"""Workflow tests for Carrier integration setup and unload."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from carrier_api import CarrierApiAuthError, CarrierApiConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_carrier import (
    _async_await_websocket_task,
    async_migrate_entry,
    async_setup_entry,
)
from custom_components.ha_carrier.const import CONFIG_FLOW_VERSION, DOMAIN
from custom_components.ha_carrier.coordinator import CarrierDataUpdateCoordinator
from custom_components.ha_carrier.exceptions import CarrierUnauthorizedError

from .conftest import PASSWORD, USERNAME, FakeCarrierApiConnection


def _config_entry_not_ready_from(error: BaseException) -> ConfigEntryNotReady:
    """Build a setup retry exception that preserves the wrapped cause."""
    config_entry_error = ConfigEntryNotReady("not ready")
    config_entry_error.__cause__ = error
    return config_entry_error


@pytest.mark.asyncio
async def test_setup_entry_loads_platforms_and_unload_cancels_websocket(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Set up the config entry through HA and unload the websocket task cleanly."""
    config_entry = await setup_integration()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.systems == carrier_api.systems
    assert config_entry.runtime_data.websocket_task is not None
    assert config_entry.runtime_data.api_connection.api_websocket.callbacks
    coordinator = config_entry.runtime_data

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert coordinator.websocket_task is None


@pytest.mark.asyncio
async def test_websocket_task_recovers_from_bad_update_data(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
) -> None:
    """Keep the websocket task alive when Carrier sends malformed update data."""
    monkeypatch.setattr("custom_components.ha_carrier.compute_backoff_delay", lambda *_: 0)
    carrier_api.api_websocket.listener_errors.append(KeyError("id"))

    config_entry = await setup_integration()
    websocket_task = config_entry.runtime_data.websocket_task

    for _ in range(10):
        await hass.async_block_till_done()
        if carrier_api.api_websocket.listener_calls >= 2:
            break
        await asyncio.sleep(0)

    assert carrier_api.api_websocket.listener_calls == 2
    assert websocket_task is not None
    assert not websocket_task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "websocket_error",
    [
        CarrierApiAuthError("unauthorized"),
        CarrierApiConnectionError("offline"),
    ],
)
async def test_websocket_task_recovers_from_carrier_api_error(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
    websocket_error: Exception,
) -> None:
    """Keep websocket listening after the Carrier API reports a recoverable failure."""
    monkeypatch.setattr("custom_components.ha_carrier.compute_backoff_delay", lambda *_: 0)
    carrier_api.api_websocket.listener_errors.append(websocket_error)

    config_entry = await setup_integration()
    websocket_task = config_entry.runtime_data.websocket_task

    for _ in range(10):
        await hass.async_block_till_done()
        if carrier_api.api_websocket.listener_calls >= 2:
            break
        await asyncio.sleep(0)

    assert carrier_api.api_websocket.listener_calls == 2
    assert websocket_task is not None
    assert not websocket_task.done()


@pytest.mark.asyncio
async def test_websocket_task_stops_after_coordinator_escalates_to_reauth(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
) -> None:
    """Stop reconnecting, and so retrying the password, once reauth is required."""
    monkeypatch.setattr("custom_components.ha_carrier.compute_backoff_delay", lambda *_: 0)
    monkeypatch.setattr("custom_components.ha_carrier.WEBSOCKET_AUTH_RECOVERY_POLL_SECONDS", 0)
    carrier_api.api_websocket.listener_errors.append(CarrierApiAuthError("bad password"))

    async def _refresh_escalates(self: CarrierDataUpdateCoordinator) -> None:
        """Simulate a coordinator refresh that crossed the reauth threshold."""
        self.last_update_success = False
        self.last_exception = ConfigEntryAuthFailed("reauth required")

    monkeypatch.setattr(CarrierDataUpdateCoordinator, "async_request_refresh", _refresh_escalates)

    config_entry = await setup_integration()
    websocket_task = config_entry.runtime_data.websocket_task
    assert websocket_task is not None

    for _ in range(10):
        await hass.async_block_till_done()
        if websocket_task.done():
            break
        await asyncio.sleep(0)

    assert websocket_task.done()
    assert websocket_task.exception() is None
    assert carrier_api.api_websocket.listener_calls == 1


@pytest.mark.asyncio
async def test_websocket_task_waits_for_coordinator_recovery_after_auth_error(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
) -> None:
    """Reconnect only after the coordinator refreshes successfully again."""
    monkeypatch.setattr("custom_components.ha_carrier.compute_backoff_delay", lambda *_: 0)
    monkeypatch.setattr("custom_components.ha_carrier.WEBSOCKET_AUTH_RECOVERY_POLL_SECONDS", 0)
    carrier_api.api_websocket.listener_errors.append(CarrierApiAuthError("rejected"))

    async def _refresh_fails(self: CarrierDataUpdateCoordinator) -> None:
        """Simulate a coordinator refresh that failed below the reauth threshold."""
        self.last_update_success = False
        self.last_exception = None

    monkeypatch.setattr(CarrierDataUpdateCoordinator, "async_request_refresh", _refresh_fails)

    config_entry = await setup_integration()
    coordinator = config_entry.runtime_data
    websocket_task = coordinator.websocket_task
    assert websocket_task is not None

    for _ in range(10):
        await hass.async_block_till_done()
        await asyncio.sleep(0)

    assert carrier_api.api_websocket.listener_calls == 1
    assert not websocket_task.done()

    coordinator.last_update_success = True
    for _ in range(10):
        await hass.async_block_till_done()
        if carrier_api.api_websocket.listener_calls >= 2:
            break
        await asyncio.sleep(0)

    assert carrier_api.api_websocket.listener_calls == 2
    assert not websocket_task.done()


@pytest.mark.asyncio
async def test_unload_ignores_websocket_task_data_update_failure(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Unload cleanly when the websocket task already failed on bad update data."""
    config_entry = await setup_integration()
    coordinator = config_entry.runtime_data

    async def fail_with_bad_websocket_data() -> None:
        """Raise the same exception family as malformed Carrier websocket payloads."""
        raise KeyError("id")

    failed_task: asyncio.Task[None] = hass.async_create_task(
        fail_with_bad_websocket_data(),
        name="failed_carrier_websocket",
    )
    coordinator.websocket_task = failed_task
    await hass.async_block_till_done()

    assert failed_task.done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert coordinator.websocket_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_error",
    [
        CarrierApiConnectionError("offline"),
        CarrierApiAuthError("unauthorized"),
        RuntimeError("websocket missing"),
    ],
)
async def test_await_websocket_task_suppresses_expected_failures(
    hass: HomeAssistant,
    task_error: Exception,
) -> None:
    """Drain failed websocket tasks for expected recoverable failure families."""

    async def fail_websocket_task() -> None:
        """Raise the configured websocket task failure."""
        raise task_error

    failed_task: asyncio.Task[None] = hass.async_create_task(
        fail_websocket_task(),
        name="failed_carrier_websocket",
    )
    await hass.async_block_till_done()

    await _async_await_websocket_task(failed_task)


@pytest.mark.asyncio
async def test_setup_entry_maps_carrier_connection_error_to_not_ready(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Convert Carrier API connection setup failures to Home Assistant retry."""
    patch_carrier_api.load_data_error = CarrierApiConnectionError("offline")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": USERNAME, "password": PASSWORD},
    )
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refresh_error", "expected_error"),
    [
        (ConfigEntryAuthFailed("reauth"), ConfigEntryAuthFailed),
        (CarrierUnauthorizedError("unauthorized"), ConfigEntryAuthFailed),
        (CarrierApiConnectionError("offline"), ConfigEntryNotReady),
        (_config_entry_not_ready_from(CarrierApiAuthError("unauthorized")), ConfigEntryAuthFailed),
        (ConfigEntryNotReady("offline"), ConfigEntryNotReady),
    ],
)
async def test_setup_entry_maps_first_refresh_errors(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    refresh_error: Exception,
    expected_error: type[Exception],
) -> None:
    """Map first-refresh exceptions to the Home Assistant setup lifecycle."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": USERNAME, "password": PASSWORD},
    )
    config_entry.add_to_hass(hass)

    async def async_first_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Raise the configured first-refresh failure."""
        raise refresh_error

    monkeypatch.setattr(
        CarrierDataUpdateCoordinator,
        "async_config_entry_first_refresh",
        async_first_refresh,
    )

    with pytest.raises(expected_error):
        await async_setup_entry(hass, config_entry)


async def _wait_for_listener_calls(
    hass: HomeAssistant, carrier_api: FakeCarrierApiConnection, calls: int
) -> None:
    """Let the websocket loop run until the listener has been entered ``calls`` times.

    Args:
        hass: Home Assistant instance.
        carrier_api: Fake Carrier API whose websocket is being watched.
        calls: Listener call count to wait for.
    """
    for _ in range(20):
        await hass.async_block_till_done()
        if carrier_api.api_websocket.listener_calls >= calls:
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_websocket_task_reconnects_after_the_listener_ends(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
) -> None:
    """Refresh and listen again when Carrier closes the websocket normally."""
    monkeypatch.setattr("custom_components.ha_carrier.compute_backoff_delay", lambda *_: 0)
    carrier_api.api_websocket.listener_returns = 1

    config_entry = await setup_integration()
    await _wait_for_listener_calls(hass, carrier_api, 2)

    assert carrier_api.api_websocket.listener_calls == 2
    assert not config_entry.runtime_data.websocket_task.done()


@pytest.mark.asyncio
async def test_websocket_data_errors_log_once_per_failure_streak(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log the first websocket data failure as an error and repeats only at debug."""
    monkeypatch.setattr("custom_components.ha_carrier.compute_backoff_delay", lambda *_: 0)
    carrier_api.api_websocket.listener_errors.extend([KeyError("id"), KeyError("id")])
    caplog.set_level(logging.DEBUG, logger="custom_components.ha_carrier")

    await setup_integration()
    await _wait_for_listener_calls(hass, carrier_api, 3)

    failures = [
        record for record in caplog.records if "websocket data update failed" in record.message
    ]
    assert [record.levelno for record in failures] == [logging.ERROR, logging.DEBUG]


@pytest.mark.asyncio
async def test_refresh_outage_logs_once_and_recovery_logs_once(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
    setup_integration: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log Carrier becoming unavailable once, not on every failed refresh."""
    monkeypatch.setattr(
        "custom_components.ha_carrier.resiliency.compute_backoff_delay", lambda *_: 0
    )
    config_entry = await setup_integration()
    coordinator = config_entry.runtime_data
    carrier_api.load_data_error = CarrierApiConnectionError("offline")
    caplog.clear()

    for _ in range(3):
        coordinator.data_flush = True
        await coordinator.async_refresh()
    carrier_api.load_data_error = None
    coordinator.data_flush = True
    await coordinator.async_refresh()

    errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.startswith("custom_components")
    ]
    assert len([record for record in errors if "Error fetching" in record.message]) == 1
    assert not [record for record in errors if record.exc_info]
    assert len([r for r in caplog.records if "data recovered" in r.message]) == 1


@pytest.mark.asyncio
async def test_setup_entry_propagates_cancellation(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never turn a cancelled setup into a retry."""

    async def cancelled_refresh(self: CarrierDataUpdateCoordinator) -> None:
        """Cancel the first refresh."""
        raise asyncio.CancelledError

    monkeypatch.setattr(
        CarrierDataUpdateCoordinator, "async_config_entry_first_refresh", cancelled_refresh
    )
    config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    config_entry.add_to_hass(hass)

    with pytest.raises(asyncio.CancelledError):
        await async_setup_entry(hass, config_entry)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "migrate_1_result", "expected", "expected_version"),
    [
        (CONFIG_FLOW_VERSION, True, True, CONFIG_FLOW_VERSION),
        (99, True, False, 99),
        (1, False, False, 1),
    ],
)
async def test_migrate_entry_handles_current_unknown_and_failed_versions(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
    migrate_1_result: bool,
    expected: bool,
    expected_version: int,
) -> None:
    """Accept current entries, refuse unknown versions, and stop on a failed step."""

    async def fake_migrate_1_to_2(**_kwargs: Any) -> bool:
        """Return the parametrized v1 migration result."""
        return migrate_1_result

    monkeypatch.setattr("custom_components.ha_carrier.migrate_1_to_2", fake_migrate_1_to_2)
    config_entry = MockConfigEntry(
        domain=DOMAIN, version=version, data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    config_entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, config_entry) is expected
    assert config_entry.version == expected_version


@pytest.mark.asyncio
async def test_unload_without_a_websocket_task(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Unload cleanly even if the websocket task was already cleared."""
    config_entry = await setup_integration()
    task = config_entry.runtime_data.websocket_task
    task.cancel()
    config_entry.runtime_data.websocket_task = None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
