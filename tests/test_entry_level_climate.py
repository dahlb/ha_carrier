"""Workflow tests for Carrier entry-level (Smart Thermostat) climate entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.components.climate.const import SERVICE_SET_HVAC_MODE, SERVICE_SET_TEMPERATURE
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest

from .conftest import FakeCarrierApiConnection, build_entry_level_system, entity_id_for_unique_id

ENTITY_UNIQUE_ID = "el123_0_entry_level_thermostat"


@pytest.mark.asyncio
async def test_entry_level_climate_registers(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Register an entry-level thermostat with the expected initial state."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.entry_level_systems = [build_entry_level_system()]
    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ENTITY_UNIQUE_ID)
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == HVACMode.COOL
    assert state.attributes["current_temperature"] == 75
    assert state.attributes["current_humidity"] == 50
    assert state.attributes["temperature"] == 78


@pytest.mark.asyncio
async def test_entry_level_set_temperature(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Set the cool set point through Home Assistant."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.entry_level_systems = [build_entry_level_system()]
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ENTITY_UNIQUE_ID)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 72},
        blocking=True,
    )
    await hass.async_block_till_done()

    name, payload = carrier_api.calls[-1]
    assert name == "update_entry_level_zone"
    assert payload["serial"] == "EL123"
    assert payload["cool_set_point"] == 72


@pytest.mark.asyncio
async def test_entry_level_set_hvac_mode(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Change the HVAC mode through Home Assistant."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.entry_level_systems = [build_entry_level_system()]
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ENTITY_UNIQUE_ID)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()

    name, payload = carrier_api.calls[-1]
    assert name == "update_entry_level_zone"
    assert payload["mode"] == "heat"
