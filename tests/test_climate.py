"""Workflow tests for Carrier climate entities and service writes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from carrier_api import ActivityTypes, FanModes, SystemModes, TemperatureUnits
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    ClimateEntityFeature,
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
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant.common import async_fire_time_changed

from custom_components.ha_carrier.climate import normalize_humidity_target
from custom_components.ha_carrier.const import FAN_AUTO, PROGRAM_READBACK_DELAY_S

from .conftest import (
    FakeCarrierApiConnection,
    build_carrier_system,
    entity_for_unique_id,
    entity_id_for_unique_id,
)


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
@pytest.mark.parametrize("expected_lingering_timers", [True])  # the 45 s read-back
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
@pytest.mark.parametrize("expected_lingering_timers", [True])  # the 45 s read-back
async def test_set_program_day_reads_the_program_back_in_full(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Follow the write with a full read, so get_schedule shows the new day.

    A plain refresh after a write is the light energy refresh, which leaves the
    program as it was until the next scheduled full refresh (up to two hours).
    """
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)
    full_reads_before = len([call for call in carrier_api.calls if call[0] == "load_data"])

    await hass.services.async_call(
        "ha_carrier",
        "set_program_day",
        {ATTR_ENTITY_ID: entity_id, "day_id": "3", "periods": NEW_PERIODS},
        blocking=True,
    )

    calls = [call[0] for call in carrier_api.calls]
    write_index = calls.index("set_program_day")
    assert "load_data" in calls[write_index + 1 :]
    assert len([c for c in calls if c == "load_data"]) == full_reads_before + 1


@pytest.mark.asyncio
async def test_set_program_day_reads_the_program_again_once_the_cloud_shows_it(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Read the program in full again after the write has had time to reach the cloud.

    Live, a full read right after the write still returned the old day; about 40 s later
    the new one. The second read makes get_schedule show the day the Carrier now runs.
    """
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)
    await hass.services.async_call(
        "ha_carrier",
        "set_program_day",
        {ATTR_ENTITY_ID: entity_id, "day_id": "3", "periods": NEW_PERIODS},
        blocking=True,
    )
    reads_after_write = len([c for c in carrier_api.calls if c[0] == "load_data"])

    freezer.tick(timedelta(seconds=PROGRAM_READBACK_DELAY_S - 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len([c for c in carrier_api.calls if c[0] == "load_data"]) == reads_after_write

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len([c for c in carrier_api.calls if c[0] == "load_data"]) == reads_after_write + 1


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


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (-5, 0),
        (0, 0),
        (2, 0),
        (3, 5),
        (42, 40),
        (43, 45),
        (45, 45),
        (60, 45),
        (37.5, 40),
    ],
)
def test_normalize_humidity_target_clamps_and_rounds_to_nearest_step(
    requested: float, expected: int
) -> None:
    """Clamp humidity to Carrier's range and round to the nearest 5 percent."""
    assert normalize_humidity_target(requested) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("requested", "expected"), [(43, 45), (42, 40)])
async def test_set_humidity_sends_nearest_carrier_step(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    requested: int,
    expected: int,
) -> None:
    """Send the nearest Carrier-accepted humidity target and show it locally."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HUMIDITY,
        {ATTR_ENTITY_ID: entity_id, ATTR_HUMIDITY: requested},
        blocking=True,
    )
    await hass.async_block_till_done()

    humidity_calls = [call for call in carrier_api.calls if call[0] == "set_config_heat_humidity"]
    assert humidity_calls[-1][1]["humidity_target"] == expected
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["humidity"] == expected
    assert state.attributes["min_humidity"] == 0
    assert state.attributes["max_humidity"] == 45


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hvac_mode", "expected_mode"),
    [
        (HVACMode.COOL, SystemModes.COOL),
        (HVACMode.HEAT, SystemModes.HEAT),
        (HVACMode.OFF, SystemModes.OFF),
        (HVACMode.HEAT_COOL, SystemModes.AUTO),
        (HVACMode.FAN_ONLY, SystemModes.FAN_ONLY),
    ],
)
async def test_set_hvac_mode_sends_the_matching_carrier_mode(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    hvac_mode: HVACMode,
    expected_mode: SystemModes,
) -> None:
    """Translate each Home Assistant HVAC mode to its Carrier system mode."""
    carrier_api.systems = [build_carrier_system(fan_enabled=True)]
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode},
        blocking=True,
    )

    assert carrier_api.calls[-1] == (
        "set_config_mode",
        {"system_serial": "ABC123", "mode": expected_mode},
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == hvac_mode


@pytest.mark.asyncio
async def test_set_hvac_mode_rejects_an_unknown_mode(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Raise for a mode Carrier has no equivalent for."""
    await setup_integration()
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    with pytest.raises(ServiceValidationError) as exc_info:
        await entity.async_set_hvac_mode("dry")
    assert exc_info.value.translation_key == "unsupported_hvac_mode"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capabilities", "expected_modes", "has_range"),
    [
        (
            {"fan": False, "cool": True, "heat": False},
            [HVACMode.OFF, HVACMode.COOL],
            False,
        ),
        (
            {"fan": False, "cool": False, "heat": True},
            [HVACMode.OFF, HVACMode.HEAT],
            False,
        ),
    ],
)
async def test_climate_offers_only_the_modes_the_system_supports(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    capabilities: dict[str, bool],
    expected_modes: list[HVACMode],
    has_range: bool,
) -> None:
    """Hide cooling, heating and fan modes the equipment cannot run."""
    system = build_carrier_system()
    system.config.mode = "off"
    system.supported_hvac_capabilities = lambda: capabilities  # type: ignore[method-assign]
    carrier_api.systems = [system]
    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1))

    assert state is not None
    assert state.attributes["hvac_modes"] == expected_modes
    assert bool(state.attributes["supported_features"] & ClimateEntityFeature.FAN_MODE) is False
    assert (
        bool(state.attributes["supported_features"] & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)
        is has_range
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carrier_mode", "conditioning", "fan", "expected_action"),
    [
        ("fanonly", "idle", FanModes.OFF, "idle"),
        ("auto", "dehumidify", FanModes.OFF, "idle"),
    ],
)
async def test_climate_reports_idle_when_the_fan_is_off(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    carrier_mode: str,
    conditioning: str,
    fan: FanModes,
    expected_action: str,
) -> None:
    """Report idle rather than fan when the blower is off."""
    system = carrier_api.systems[0]
    system.config.mode = carrier_mode
    system.status.zones[0].conditioning = conditioning
    system.status.zones[0].fan = fan
    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1))

    assert state is not None
    assert state.attributes["hvac_action"] == expected_action


@pytest.mark.asyncio
async def test_climate_uses_celsius_and_hides_humidity_without_a_humidifier(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Use half-degree steps in Celsius and drop humidity control without a humidifier."""
    system = carrier_api.systems[0]
    system.status.temperature_unit = TemperatureUnits.CELSIUS
    system.config.humidifier_enabled = False
    await setup_integration()
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    assert entity.target_temperature_step == 0.5
    assert entity.target_humidity is None
    assert not entity.supported_features & ClimateEntityFeature.TARGET_HUMIDITY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carrier_mode", "data", "expected_heat", "expected_cool"),
    [
        ("cool", {ATTR_TEMPERATURE: 72}, "69", "72"),
        ("heat", {ATTR_TEMPERATURE: 66}, "66", "75"),
    ],
)
async def test_set_temperature_keeps_the_other_set_point_in_single_modes(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    carrier_mode: str,
    data: dict[str, Any],
    expected_heat: str,
    expected_cool: str,
) -> None:
    """Change only the active set point and keep the manual activity's other one."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.systems[0].config.mode = carrier_mode
    await setup_integration(options={"infinite_holds": False})
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
    )

    manual = next(call for call in carrier_api.calls if call[0] == "set_config_manual_activity")
    assert (float(manual[1]["heat_set_point"]), float(manual[1]["cool_set_point"])) == (
        float(expected_heat),
        float(expected_cool),
    )
    hold = next(call for call in carrier_api.calls if call[0] == "set_config_hold")
    # With infinite holds off the hold ends at the next scheduled activity.
    assert hold[1]["hold_until"] is not None


@pytest.mark.asyncio
async def test_set_temperature_validates_set_points_and_manual_activity(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Reject a heat/cool request without both set points, and a zone without MANUAL."""
    await setup_integration()
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    with pytest.raises(ServiceValidationError) as missing:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 70})
    assert missing.value.translation_key == "set_points_unresolved"

    carrier_api.systems[0].config.zones[0].activities[:] = [
        activity
        for activity in carrier_api.systems[0].config.zones[0].activities
        if activity.type != ActivityTypes.MANUAL
    ]
    with pytest.raises(HomeAssistantError) as no_manual:
        await entity.async_set_temperature(**{ATTR_TARGET_TEMP_LOW: 68, ATTR_TARGET_TEMP_HIGH: 74})
    assert no_manual.value.translation_key == "manual_activity_unavailable"


@pytest.mark.asyncio
async def test_set_preset_mode_without_a_matching_activity_keeps_set_points(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Hold an activity the zone has no set points for without touching set points."""
    await setup_integration()
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)
    status_zone = carrier_api.systems[0].status.zones[0]
    before = (status_zone.heat_set_point, status_zone.cool_set_point)

    await entity.async_set_preset_mode("wake")

    assert carrier_api.calls[-1][0] == "set_config_hold"
    assert carrier_api.calls[-1][1]["activity_type"] == ActivityTypes.WAKE
    assert (status_zone.heat_set_point, status_zone.cool_set_point) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carrier_mode", "data", "expected_heat", "expected_cool"),
    [
        ("cool", {ATTR_TEMPERATURE: 73}, "68", "73"),
        ("heat", {ATTR_TEMPERATURE: 67}, "67", "74"),
    ],
)
async def test_set_activity_setpoint_keeps_the_other_set_point_in_single_modes(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    carrier_mode: str,
    data: dict[str, Any],
    expected_heat: str,
    expected_cool: str,
) -> None:
    """Edit only the active set point of the current activity in heat or cool mode."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    carrier_api.systems[0].config.mode = carrier_mode
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    await hass.services.async_call(
        "ha_carrier",
        "set_activity_setpoint",
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
    )

    write = carrier_api.calls[-1]
    assert write[0] == "set_config_activity"
    assert (float(write[1]["heat_set_point"]), float(write[1]["cool_set_point"])) == (
        float(expected_heat),
        float(expected_cool),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "clear_activities", "error", "translation_key"),
    [
        (
            {ATTR_TEMPERATURE: 70},
            False,
            ServiceValidationError,
            "activity_setpoint_missing_temperature",
        ),
        (
            {ATTR_TEMPERATURE: 70, "activity": "wake"},
            False,
            ServiceValidationError,
            "activity_not_configured",
        ),
        ({ATTR_TEMPERATURE: 70}, True, HomeAssistantError, "current_activity_unavailable"),
    ],
)
async def test_set_activity_setpoint_rejects_unresolvable_requests(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    data: dict[str, Any],
    clear_activities: bool,
    error: type[HomeAssistantError],
    translation_key: str,
) -> None:
    """Raise a translated error when the activity or set points cannot be resolved."""
    await setup_integration()
    if clear_activities:
        carrier_api.systems[0].config.zones[0].activities.clear()
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    with pytest.raises(error) as raised:
        await entity.async_set_activity_setpoint(**data)

    assert raised.value.translation_key == translation_key


@pytest.mark.asyncio
async def test_set_program_day_requires_a_known_etag(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Refuse to write a program day before any etag is known."""
    await setup_integration()
    system = carrier_api.systems[0]
    program = system.config.zones[0].program_json
    program.pop("etag", None)
    for day in program["day"]:
        day.pop("etag", None)
    system.config.etag = None
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_program_day(day_id="0", periods=NEW_PERIODS)

    assert raised.value.translation_key == "program_etag_unknown"


@pytest.mark.asyncio
async def test_required_zone_api_id_raises_without_a_zone(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Fail clearly if a climate entity somehow lost its zone binding."""
    await setup_integration()
    entity = entity_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1)
    entity.zone_api_id = None

    with pytest.raises(ValueError, match="No zone api id defined"):
        _ = entity._required_zone_api_id


@pytest.mark.asyncio
async def test_climate_no_longer_carries_sensor_readings_as_attributes(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Keep schedule and hold attributes, but leave readings to their own sensors."""
    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, CLIMATE_DOMAIN, ZONE_1))

    assert state is not None
    assert not {"conditioning", "blower_rpm", "damper_position"} & set(state.attributes)
    assert {"status_mode", "hold_activity", "hold_until", "next_activity_time"} <= set(
        state.attributes
    )
