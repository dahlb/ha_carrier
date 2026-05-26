"""Workflow tests for Carrier integration setup and unload."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest

from .conftest import FakeCarrierApiConnection


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
