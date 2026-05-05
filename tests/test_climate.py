"""Workflow tests for Carrier climate entities and service writes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    HVACMode,
)
from homeassistant.components.climate.const import (
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
import pytest

from custom_components.ha_carrier.const import FAN_AUTO

from .conftest import FakeCarrierApiConnection, entity_id_for_unique_id


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
