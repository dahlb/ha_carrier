"""Workflow tests for Carrier entry-level (Smart Thermostat) climate entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACAction, HVACMode
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest

from .conftest import (
    FakeCarrierApiConnection,
    build_entry_level_system,
    entity_for_unique_id,
    entity_id_for_unique_id,
)

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


async def _entry_level_entity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    *,
    mode: str = "cool",
) -> Any:
    """Set up one entry-level thermostat and return its live entity object.

    Args:
        hass: Home Assistant instance.
        carrier_api: Fake Carrier API.
        setup_integration: Integration setup helper.
        mode: Zone mode reported by Carrier.

    Returns:
        Any: The ``EntryLevelThermostat`` entity.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.entry_level_systems = [build_entry_level_system(mode=mode)]
    await setup_integration()
    return entity_for_unique_id(hass, CLIMATE_DOMAIN, ENTITY_UNIQUE_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "stage", "action", "target", "low", "high"),
    [
        ("cool", "Cooling Stage 1", HVACAction.COOLING, 78, None, None),
        ("heat", "Heat Stage 2", HVACAction.HEATING, 62, None, None),
        ("off", "Idle", HVACAction.OFF, None, None, None),
        ("auto", "Idle", HVACAction.IDLE, None, 62, 78),
        ("auto", "", None, None, 62, 78),
    ],
)
async def test_entry_level_reports_action_and_set_points_for_each_mode(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    mode: str,
    stage: str,
    action: HVACAction | None,
    target: float | None,
    low: float | None,
    high: float | None,
) -> None:
    """Derive the action from the stage and show the set points the mode uses."""
    entity = await _entry_level_entity(hass, carrier_api, setup_integration, mode=mode)
    carrier_api.entry_level_systems[0].zones[0].stage_status = stage

    assert entity.hvac_action == action
    assert entity.native_target_temperature == target
    assert entity.native_target_temperature_low == low
    assert entity.native_target_temperature_high == high


@pytest.mark.asyncio
async def test_entry_level_reports_nothing_without_its_zone(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Return no readings, and refuse writes, once the system disappears."""
    entity = await _entry_level_entity(hass, carrier_api, setup_integration)
    entity.coordinator.entry_level_systems = []

    assert entity.available is False
    assert entity.hvac_mode is None
    assert entity.hvac_action is None
    assert entity.native_target_temperature is None
    assert entity.native_target_temperature_low is None
    assert entity.native_target_temperature_high is None
    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 70})
    assert raised.value.translation_key == "entry_level_zone_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "data", "expected_heat", "expected_cool"),
    [
        ("heat", {ATTR_TEMPERATURE: 65}, 65, 78),
        ("auto", {ATTR_TARGET_TEMP_LOW: 64, ATTR_TARGET_TEMP_HIGH: 77}, 64, 77),
    ],
)
async def test_entry_level_set_temperature_writes_the_right_set_points(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    mode: str,
    data: dict[str, float],
    expected_heat: float,
    expected_cool: float,
) -> None:
    """Write the heat set point in heat mode and both set points in heat/cool."""
    entity = await _entry_level_entity(hass, carrier_api, setup_integration, mode=mode)

    await entity.async_set_temperature(**data)

    name, payload = carrier_api.calls[-1]
    assert name == "update_entry_level_zone"
    assert (payload["heat_set_point"], payload["cool_set_point"]) == (
        expected_heat,
        expected_cool,
    )


@pytest.mark.asyncio
async def test_entry_level_turn_on_off_and_reject_unknown_modes(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Turn off to off, turn on to cool, and refuse modes Carrier lacks."""
    entity = await _entry_level_entity(hass, carrier_api, setup_integration)

    await entity.async_turn_off()
    assert carrier_api.calls[-1][1]["mode"] == "off"
    await entity.async_turn_on()
    assert carrier_api.calls[-1][1]["mode"] == "cool"

    entity.coordinator.entry_level_systems = []
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    assert carrier_api.calls[-1][1]["mode"] == "heat"

    with pytest.raises(ValueError, match="unsupported mode"):
        await entity.async_set_hvac_mode(HVACMode.DRY)
