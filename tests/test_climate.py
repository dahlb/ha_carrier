"""Workflow tests for Carrier climate entities and service writes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from carrier_api import ActivityTypes, FanModes
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_FAN_MODE,
    ATTR_HUMIDITY,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    SERVICE_SET_HUMIDITY,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.ha_carrier.const import FAN_AUTO

from .conftest import FakeCarrierApiConnection, build_carrier_system, entity_id_for_unique_id


@pytest.mark.asyncio
async def test_climate_platform_registers_zone_thermostat(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Register a zone thermostat with the expected initial HVAC state."""
    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == HVACMode.HEAT_COOL
    assert state.attributes["current_temperature"] == 21.1
    assert FAN_AUTO in state.attributes["fan_modes"]


@pytest.mark.asyncio
async def test_climate_platform_uses_config_fan_capability(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Omit fan controls when Carrier config reports fan support is disabled."""
    carrier_api.systems = [build_carrier_system(fan_enabled=False)]

    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")
    state = hass.states.get(entity_id)

    assert state is not None
    assert "fan_mode" not in state.attributes
    assert "fan_modes" not in state.attributes
    assert HVACMode.FAN_ONLY not in state.attributes["hvac_modes"]


@pytest.mark.asyncio
async def test_climate_preset_mode_uses_status_activity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Prefer Carrier's status activity over matching configured setpoints."""
    carrier_api.systems = [build_carrier_system()]
    system = carrier_api.systems[0]
    system.status.zones[0].current_status_activity_type = ActivityTypes.AWAY
    system.status.zones[0].heat_set_point = 68
    system.status.zones[0].cool_set_point = 74

    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.attributes["preset_mode"] == "away"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "data", "expected_calls"),
    [
        (
            SERVICE_SET_HVAC_MODE,
            {ATTR_HVAC_MODE: HVACMode.COOL},
            ["set_config_mode"],
        ),
        (
            SERVICE_SET_PRESET_MODE,
            {ATTR_PRESET_MODE: "away"},
            ["set_config_hold"],
        ),
        (
            SERVICE_SET_TEMPERATURE,
            {ATTR_TARGET_TEMP_LOW: 20, ATTR_TARGET_TEMP_HIGH: 24},
            ["set_config_manual_activity", "set_config_hold"],
        ),
        (
            SERVICE_SET_HUMIDITY,
            {ATTR_HUMIDITY: 43},
            ["set_config_heat_humidity"],
        ),
    ],
)
async def test_climate_services_call_carrier_api_and_update_local_state(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    service: str,
    data: dict[str, Any],
    expected_calls: list[str],
) -> None:
    """Exercise representative thermostat service workflows through HA."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        service,
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert [call[0] for call in carrier_api.calls if call[0] != "load_data"][
        -len(expected_calls) :
    ] == (expected_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carrier_mode", "conditioning", "fan", "expected_mode", "expected_action"),
    [
        ("cool", "cooling", FanModes.OFF, HVACMode.COOL, "cooling"),
        ("heat", "gasheat", FanModes.OFF, HVACMode.HEAT, "heating"),
        ("off", "idle", FanModes.OFF, HVACMode.OFF, "off"),
        ("fanonly", "idle", FanModes.LOW, HVACMode.FAN_ONLY, "fan"),
        ("unknown", "vent", FanModes.LOW, "unknown", "fan"),
    ],
)
async def test_climate_platform_maps_hvac_modes_and_actions(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    carrier_mode: str,
    conditioning: str,
    fan: FanModes,
    expected_mode: str,
    expected_action: str,
) -> None:
    """Map Carrier system modes and zone conditioning to HA climate attributes."""
    carrier_api.systems = [build_carrier_system()]
    system = carrier_api.systems[0]
    system.config.mode = carrier_mode
    system.status.zones[0].conditioning = conditioning
    system.status.zones[0].fan = fan

    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == expected_mode
    assert state.attributes["hvac_action"] == expected_action


@pytest.mark.asyncio
async def test_climate_resume_preset_refreshes_remote_state(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Resume scheduled programming through the Carrier API and request refresh."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_PRESET_MODE: "resume"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert "resume_schedule" in [call[0] for call in carrier_api.calls]


@pytest.mark.asyncio
async def test_climate_fan_mode_service_updates_current_activity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Write the active activity fan mode through the Carrier API."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_FAN_MODE: "high"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert carrier_api.calls[-1][0] == "update_fan"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["fan_mode"] == "high"


@pytest.mark.asyncio
async def test_climate_fan_mode_service_uses_status_activity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Write fan settings against Carrier's live status activity."""
    carrier_api.systems = [build_carrier_system()]
    system = carrier_api.systems[0]
    system.config.zones[0].hold = True
    system.config.zones[0].hold_activity = ActivityTypes.AWAY
    system.status.zones[0].current_status_activity_type = ActivityTypes.HOME
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_FAN_MODE: "high"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert carrier_api.calls[-1] == (
        "update_fan",
        {
            "system_serial": "ABC123",
            "zone_id": "1",
            "activity_type": ActivityTypes.HOME,
            "fan_mode": FanModes.HIGH,
        },
    )


@pytest.mark.asyncio
async def test_climate_fan_mode_requires_current_activity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Raise a HA error when Carrier omits the current activity profile."""
    carrier_api.systems = [build_carrier_system()]
    carrier_api.systems[0].config.zones[0].activities.clear()
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    with pytest.raises(HomeAssistantError, match="Current activity unavailable"):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_FAN_MODE: "high"},
            blocking=True,
        )
