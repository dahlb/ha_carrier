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
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
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
    carrier_api.systems = [build_carrier_system(fan_enabled=True)]
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
async def test_climate_setpoint_prefers_config_activity_over_stale_status(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Surface the config activity set point, not a stale status clsp/htsp.

    The status zone's set points only refresh on the periodic full poll, so a
    recent change can leave clsp/htsp stale while the resolved config activity is
    current. The entity should report the config activity value.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.systems = [build_carrier_system()]
    system = carrier_api.systems[0]
    system.config.mode = "cool"
    # Fresh set point lives on the resolved (home) config activity...
    home_activity = system.config.zones[0].find_activity(ActivityTypes.HOME)
    assert home_activity is not None
    home_activity.cool_set_point = 63
    # ...while the status zone still reports a stale cool set point.
    system.status.zones[0].cool_set_point = 71

    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == HVACMode.COOL
    assert state.attributes["temperature"] == 63


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
    carrier_api.systems = [build_carrier_system(fan_enabled=True)]
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


ZONE_1 = "abc123_zone_1_thermostat"
NEW_PERIODS: list[dict[str, str]] = [
    {"id": "1", "enabled": "on", "time": "06:15", "activity": "home"},
    {"id": "2", "enabled": "on", "time": "22:00", "activity": "sleep"},
]


@pytest.mark.asyncio
async def test_get_schedule_returns_the_program_and_activities(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Return the zone's program with its etags and its activity set points."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    response = await hass.services.async_call(
        "ha_carrier",
        "get_schedule",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )

    schedule = response[entity_id]
    assert schedule["zone_id"] == "1"
    assert schedule["config_etag"] == "etag"
    assert schedule["program_etag"] == "program-etag"
    assert sorted(schedule["program"]) == [str(day_id) for day_id in range(7)]
    assert schedule["program"]["3"] == {
        "etag": "day-etag-3",
        "periods": [
            {"id": "1", "activity": "home", "time": "00:00", "enabled": "on"},
            {"id": "2", "activity": "sleep", "time": "23:59", "enabled": "on"},
        ],
    }
    assert schedule["activities"]["sleep"] == {"htsp": 66.0, "clsp": 76.0, "fan": "off"}


@pytest.mark.asyncio
async def test_set_program_day_sends_the_periods_with_the_day_etag(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Send the day's periods with that day's etag unless one is given."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    await hass.services.async_call(
        "ha_carrier",
        "set_program_day",
        {ATTR_ENTITY_ID: entity_id, "day_id": "3", "periods": NEW_PERIODS},
        blocking=True,
    )
    await hass.services.async_call(
        "ha_carrier",
        "set_program_day",
        {ATTR_ENTITY_ID: entity_id, "day_id": "3", "periods": NEW_PERIODS, "etag": "etag"},
        blocking=True,
    )

    writes = [call[1] for call in carrier_api.calls if call[0] == "set_program_day"]
    assert writes == [
        {
            "system_serial": "ABC123",
            "zone_id": "1",
            "day_id": "3",
            "periods": NEW_PERIODS,
            "etag": "day-etag-3",
        },
        {
            "system_serial": "ABC123",
            "zone_id": "1",
            "day_id": "3",
            "periods": NEW_PERIODS,
            "etag": "etag",
        },
    ]


@pytest.mark.asyncio
async def test_set_program_day_rejects_an_unknown_day(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Refuse a day the zone's program does not have, without writing."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "ha_carrier",
            "set_program_day",
            {ATTR_ENTITY_ID: entity_id, "day_id": "9", "periods": NEW_PERIODS},
            blocking=True,
        )

    assert not [call for call in carrier_api.calls if call[0] == "set_program_day"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "expected_activity", "expected_heat", "expected_cool"),
    [
        ({}, ActivityTypes.HOME, "68.0", "74.0"),
        ({"activity": "sleep"}, ActivityTypes.SLEEP, "68.0", "74.0"),
    ],
)
async def test_set_activity_setpoint_edits_the_named_or_current_activity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    data: dict[str, str],
    expected_activity: ActivityTypes,
    expected_heat: str,
    expected_cool: str,
) -> None:
    """Edit the named activity, or the current one when none is named, without a hold."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    await hass.services.async_call(
        "ha_carrier",
        "set_activity_setpoint",
        {ATTR_ENTITY_ID: entity_id, ATTR_TARGET_TEMP_LOW: 68, ATTR_TARGET_TEMP_HIGH: 74, **data},
        blocking=True,
    )

    writes = [call for call in carrier_api.calls if call[0] != "load_data"]
    assert writes[-1] == (
        "set_config_activity",
        {
            "system_serial": "ABC123",
            "zone_id": "1",
            "activity_type": expected_activity,
            "heat_set_point": expected_heat,
            "cool_set_point": expected_cool,
        },
    )
    assert "set_config_hold" not in [call[0] for call in writes]
