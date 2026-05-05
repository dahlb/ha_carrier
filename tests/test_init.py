"""Workflow tests for Carrier integration setup and unload."""

from __future__ import annotations

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
