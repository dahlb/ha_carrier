"""Workflow tests for Carrier integration setup and unload."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from carrier_api import CarrierApiAuthError, CarrierApiConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_carrier import _async_await_websocket_task, async_setup_entry
from custom_components.ha_carrier.carrier_data_update_coordinator import (
    CarrierDataUpdateCoordinator,
)
from custom_components.ha_carrier.const import DOMAIN
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
