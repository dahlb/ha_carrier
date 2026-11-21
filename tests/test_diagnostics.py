"""Workflow tests for Carrier diagnostics output."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest

from custom_components.ha_carrier.diagnostics import async_get_config_entry_diagnostics

from .conftest import FakeCarrierApiConnection, build_entry_level_system


@pytest.mark.asyncio
async def test_diagnostics_redacts_config_entry_and_includes_device_entities(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Build diagnostics with redaction, HVAC capabilities, and activity data."""
    config_entry = await setup_integration()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["entry"]["data"][CONF_USERNAME] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["ABC123"]["mapped_data"]["serial"] == "**REDACTED**"
    assert diagnostics["ABC123"]["mapped_data"]["supported_hvac_capabilities"] == {
        "cool": True,
        "fan": True,
        "heat": True,
    }
    assert (
        diagnostics["ABC123"]["mapped_data"]["config"]["zones"][0]["current_activity"][
            "from_status"
        ]["type"]
        == "home"
    )
    assert diagnostics["ABC123"]["device"]["entities"]


@pytest.mark.asyncio
async def test_diagnostics_include_coordinator_state_and_entry_level_systems(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Report coordinator health and redacted entry-level payloads."""
    carrier_api.entry_level_systems = [build_entry_level_system()]
    config_entry = await setup_integration()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    coordinator = diagnostics["coordinator"]
    assert coordinator["last_update_success"] is True
    assert coordinator["last_exception"] is None
    assert coordinator["data_flush"] is False
    assert coordinator["websocket_task_running"] is True
    assert coordinator["timestamp_all_data"] is not None
    assert coordinator["update_interval_seconds"] > 0
    assert coordinator["full_refresh_interval_seconds"] > 0
    assert coordinator["resiliency"] == {
        "consecutive_unauthorized": 0,
        "consecutive_transient": 0,
    }

    entry_level = diagnostics["entry_level_systems"]
    assert entry_level["loaded"] is True
    [system] = entry_level["systems"]
    assert system["serial"] == "**REDACTED**"
    assert system["location_id"] == "**REDACTED**"
    assert system["connection"]["deviceId"] == "**REDACTED**"
    assert system["model"] == "TSTATCCEEF-01"
