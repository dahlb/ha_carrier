"""Workflow tests for Carrier sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from carrier_api import TemperatureUnits
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest

from custom_components.ha_carrier.const import DOMAIN
from custom_components.ha_carrier.sensor import CarrierSensor

from .conftest import FakeCarrierApiConnection, build_carrier_system, entity_id_for_unique_id

# Home Assistant converts the 1200 CFM fixture airflow to m³/min under the metric unit system.
AIRFLOW_METRIC_STATE = "33.9802159104"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unique_id", "expected_state"),
    [
        ("abc123_outdoor_temperature", "4.44444444444444"),
        ("abc123_filter_remaining", "80"),
        ("abc123_airflow", AIRFLOW_METRIC_STATE),
        ("abc123_static_pressure", "0.2"),
        ("abc123_zone_1_temperature", "21.1111111111111"),
        ("abc123_zone_1_humidity", "45"),
        ("abc123_cooling_energy_year_to_date", "100"),
        ("abc123_cooling_energy_yesterday", "1"),
        ("abc123_cooling_energy_last_month", "10"),
    ],
)
async def test_sensor_platform_registers_representative_state_sensors(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    unique_id: str,
    expected_state: str,
) -> None:
    """Register representative system, zone, timestamp, and energy sensors."""
    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == expected_state


@pytest.mark.asyncio
async def test_energy_sensors_use_carrier_api_energy_helpers(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Register energy sensors from mapped Carrier API energy helpers."""
    carrier_api.systems = [build_carrier_system()]

    await setup_integration()

    expected_states = {
        "abc123_cooling_energy_year_to_date": "100",
        "abc123_cooling_energy_yesterday": "1",
        "abc123_cooling_energy_last_month": "10",
    }
    for unique_id, expected_state in expected_states.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == expected_state
    assert er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "abc123_gas_energy_year_to_date"
    )


@pytest.mark.asyncio
async def test_energy_sensor_names_use_carrier_api_metric_labels_with_stable_unique_ids(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Use Carrier API metric labels without changing entity unique IDs."""
    await setup_integration()

    expected_names = {
        "abc123_hp_heat_energy_year_to_date": "Home Heat Pump Heat Energy Year to Date",
        "abc123_hp_heat_energy_yesterday": "Home Heat Pump Heat Energy Yesterday",
        "abc123_hp_heat_energy_last_month": "Home Heat Pump Heat Energy Last Month",
    }
    for unique_id, expected_name in expected_names.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.attributes["friendly_name"] == expected_name


@pytest.mark.asyncio
async def test_sensor_platform_registers_propane_and_lifecycle_sensors(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Register optional propane, humidifier, UV, and variable-capacity sensors."""
    carrier_api.systems = [build_carrier_system()]
    system = carrier_api.systems[0]
    system.config.fuel_type = "propane"
    system.config.gas_unit = "gallon"

    await setup_integration()

    expected_states = {
        "abc123_propane_consumption_year_to_date": "12.3854677194896",
        "abc123_propane_usage_year_to_date": "3.33141984548898",
        "abc123_humidifier_remaining": "70",
        "abc123_uv_lamp_remaining": "90",
        "abc123_odu_var": "unavailable",
        "abc123_idu_status": "idle",
    }
    for unique_id, expected_state in expected_states.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == expected_state


@pytest.mark.asyncio
async def test_unit_status_sensors_report_state_and_split_readings_into_sensors(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Report unit status as state only; unit readings are their own sensors."""
    carrier_api.systems = [build_carrier_system()]

    await setup_integration()

    for unique_id in ("abc123_odu_status", "abc123_idu_status"):
        state = hass.states.get(entity_id_for_unique_id(hass, "sensor", unique_id))

        assert state is not None
        assert state.state == "idle"
        assert not {
            "airflow_cfm",
            "blower_rpm",
            "operational_status",
            "static_pressure",
            "line_voltage",
        } & set(state.attributes)
    blower = hass.states.get(entity_id_for_unique_id(hass, "sensor", "abc123_blower_speed"))
    assert blower is not None
    assert blower.state == "500"
    assert blower.attributes["unit_of_measurement"] == "rpm"

    expected_states = {
        "abc123_airflow": AIRFLOW_METRIC_STATE,
        "abc123_static_pressure": "0.2",
    }
    for unique_id, expected_state in expected_states.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == expected_state


@pytest.mark.asyncio
async def test_unit_status_sensors_do_not_expose_raw_attributes_when_mapped_units_are_missing(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Avoid exposing raw status attributes when mapped unit helpers are missing."""
    carrier_api.systems = [build_carrier_system()]
    status = carrier_api.systems[0].status
    status.outdoor_unit = None
    status.indoor_unit = None

    await setup_integration()

    expected_states = {
        "abc123_airflow": AIRFLOW_METRIC_STATE,
        "abc123_static_pressure": "0.2",
    }
    for unique_id, expected_state in expected_states.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == expected_state

    for unique_id in ("abc123_odu_status", "abc123_idu_status"):
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert not (
            {
                "airflow_cfm",
                "blwrpm",
                "blower_rpm",
                "cfm",
                "operational_status",
                "opstat",
                "static_pressure",
                "statpress",
                "type",
            }
            & set(state.attributes)
        )


@pytest.mark.asyncio
async def test_airflow_and_static_pressure_fall_back_when_mapped_fields_are_missing(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Use top-level status values when mapped indoor unit fields are partial."""
    carrier_api.systems = [build_carrier_system()]
    status = carrier_api.systems[0].status
    indoor_unit = status.indoor_unit
    assert indoor_unit is not None
    status.indoor_unit = type(indoor_unit)({"opstat": "idle", "blwrpm": 500})

    await setup_integration()

    expected_states = {
        "abc123_airflow": AIRFLOW_METRIC_STATE,
        "abc123_static_pressure": "0.2",
    }
    for unique_id, expected_state in expected_states.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == expected_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outdoor_status", "expected_state"),
    [
        ("42", "on"),
        ("off", "off"),
        ("standby", "standby"),
    ],
)
async def test_sensor_platform_maps_outdoor_operational_status_variants(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    outdoor_status: str,
    expected_state: str,
) -> None:
    """Map numeric and text outdoor-unit statuses to stable sensor states."""
    carrier_api.systems = [build_carrier_system()]
    carrier_api.systems[0].status.outdoor_unit_operational_status = outdoor_status

    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, "sensor", "abc123_odu_status")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == expected_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operational_status", "expected_state"),
    [("67", "67.0"), ("off", "0.0"), ("idle", "unavailable")],
)
async def test_modulating_furnace_registers_idu_var_sensor(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    operational_status: str,
    expected_state: str,
) -> None:
    """Report a modulating furnace's operational status as a percentage."""
    carrier_api.systems = [build_carrier_system()]
    system = carrier_api.systems[0]
    assert system.status.indoor_unit is not None
    system.status.indoor_unit.type = "furnacemodulating"
    system.status.indoor_unit_operational_status = operational_status

    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, "sensor", "abc123_idu_var"))

    assert state is not None
    assert state.state == expected_state


def _refresh_entities(hass: HomeAssistant, entry: Any) -> None:
    """Push the coordinator's current (mutated) data to every entity.

    Args:
        hass: Home Assistant instance.
        entry: Loaded Carrier config entry.
    """
    entry.runtime_data.async_update_listeners()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unique_id", "mutate"),
    [
        ("abc123_filter_remaining", lambda system: setattr(system.status, "filter_used", None)),
        (
            "abc123_humidifier_remaining",
            lambda system: setattr(system.status, "humidity_level", None),
        ),
        ("abc123_uv_lamp_remaining", lambda system: setattr(system.status, "uv_lamp_level", None)),
        (
            "abc123_airflow",
            lambda system: (
                setattr(system.status.indoor_unit, "airflow_cfm", None),
                setattr(system.status, "airflow_cfm", None),
            ),
        ),
        (
            "abc123_odu_status",
            lambda system: setattr(system.status, "outdoor_unit_operational_status", None),
        ),
        (
            "abc123_gas_usage_year_to_date",
            lambda system: setattr(
                system.energy, "value_for_period_metric", lambda period, metric: None
            ),
        ),
    ],
)
async def test_sensors_go_unavailable_when_carrier_omits_their_value(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    unique_id: str,
    mutate: Callable[[Any], Any],
) -> None:
    """Mark a sensor unavailable instead of reporting a stale or bogus value."""
    entry = await setup_integration()
    entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    mutate(carrier_api.systems[0])
    _refresh_entities(hass, entry)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_propane_sensor_goes_unavailable_without_gas_usage(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Mark the propane sensor unavailable when Carrier omits year-to-date gas usage."""
    system = carrier_api.systems[0]
    system.config.fuel_type = "propane"
    entry = await setup_integration()
    entity_id = entity_id_for_unique_id(hass, "sensor", "abc123_propane_consumption_year_to_date")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    system.energy.value_for_period_metric = lambda period, metric: None
    _refresh_entities(hass, entry)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gas_unit", "expected_state"),
    [
        # 300 kBTU reported; gjoule values are hundredths of a gigajoule.
        ("gjoule", str(round(300 / 100 * 25.5, 2))),
        ("unknown", STATE_UNAVAILABLE),
    ],
)
async def test_gas_usage_sensor_converts_or_rejects_each_gas_unit(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    gas_unit: str,
    expected_state: str,
) -> None:
    """Convert gigajoules to cubic meters and refuse an unknown gas unit."""
    carrier_api.systems[0].config.gas_unit = gas_unit
    await setup_integration()

    state = hass.states.get(
        entity_id_for_unique_id(hass, "sensor", "abc123_gas_usage_year_to_date")
    )

    assert state is not None
    if expected_state == STATE_UNAVAILABLE:
        assert state.state == STATE_UNAVAILABLE
    else:
        assert float(state.state) == pytest.approx(float(expected_state))


@pytest.mark.asyncio
async def test_sensor_platform_skips_sensors_for_missing_equipment(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Skip sensors for missing equipment and read zone temperatures in Celsius."""
    system = build_carrier_system(has_heat_pump=False)
    system.config.uv_enabled = False
    system.config.humidifier_enabled = False
    system.config.fuel_type = None
    system.status.temperature_unit = TemperatureUnits.CELSIUS
    carrier_api.systems = [system]
    await setup_integration()

    registry = er.async_get(hass)
    for unique_id in (
        "abc123_odu_var",
        "abc123_uv_lamp_remaining",
        "abc123_humidifier_remaining",
        "abc123_gas_usage_year_to_date",
    ):
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
    zone_temperature = hass.states.get(
        entity_id_for_unique_id(hass, "sensor", "abc123_zone_1_temperature")
    )
    # A Celsius thermostat reports its 70 degree reading as Celsius, not converted.
    assert zone_temperature is not None
    assert zone_temperature.state == "70.0"


@pytest.mark.asyncio
async def test_base_sensor_requires_a_serial_and_defaults_to_unavailable(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Reject a missing serial and stay unavailable until a subclass reads data."""
    entry = await setup_integration()
    coordinator = entry.runtime_data

    with pytest.raises(ValueError, match="serial is required"):
        CarrierSensor("Test", coordinator, None)

    assert CarrierSensor("Test", coordinator, "ABC123").available is False


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp_type", ["all_data", "websocket", "energy"])
async def test_last_updated_sensors_are_disabled_by_default(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    timestamp_type: str,
) -> None:
    """Register the diagnostic last-updated sensors disabled until a user enables them."""
    await setup_integration()

    entry = er.async_get(hass).async_get(
        entity_id_for_unique_id(hass, "sensor", f"abc123_{timestamp_type}_last_updated")
    )

    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(entry.entity_id) is None


# Readings from carrier_api's diagnostic-device-info fixture.
OUTDOOR_DIAGNOSTICS: dict[str, Any] = {
    "blower_rpm": 940,
    "outdoor_coil_temperature": 76.0,
    "discharge_temperature": 140.0,
    "suction_temperature": 78.0,
    "suction_pressure": 193.0,
    "suction_superheat": 4.65234375,
    "line_voltage": 246,
    "lockout_active": False,
}
OUTDOOR_SENSOR_IDS = (
    "abc123_outdoor_fan_speed",
    "abc123_outdoor_coil_temperature",
    "abc123_discharge_temperature",
    "abc123_suction_temperature",
    "abc123_suction_pressure",
    "abc123_suction_superheat",
    "abc123_line_voltage",
)


async def _enable_and_reload(
    hass: HomeAssistant, entry: Any, entities: list[tuple[str, str]]
) -> None:
    """Enable disabled-by-default entities and reload so they are added.

    Args:
        hass: Home Assistant instance.
        entry: Loaded Carrier config entry.
        entities: ``(domain, unique_id)`` pairs to enable.
    """
    registry = er.async_get(hass)
    for domain, unique_id in entities:
        registry.async_update_entity(
            entity_id_for_unique_id(hass, domain, unique_id), disabled_by=None
        )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_outdoor_unit_diagnostic_sensors_are_disabled_until_enabled(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Register outdoor readings disabled, then report them with units once enabled."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    outdoor_unit = carrier_api.systems[0].status.outdoor_unit
    for field, value in OUTDOOR_DIAGNOSTICS.items():
        setattr(outdoor_unit, field, value)
    entry = await setup_integration()
    registry = er.async_get(hass)
    for unique_id in OUTDOOR_SENSOR_IDS:
        registry_entry = registry.async_get(entity_id_for_unique_id(hass, "sensor", unique_id))
        assert registry_entry is not None
        assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC

    await _enable_and_reload(
        hass, entry, [("sensor", unique_id) for unique_id in OUTDOOR_SENSOR_IDS]
    )

    expected = {
        "abc123_outdoor_fan_speed": ("940", "rpm"),
        "abc123_outdoor_coil_temperature": ("76.0", "°F"),
        "abc123_discharge_temperature": ("140.0", "°F"),
        "abc123_suction_temperature": ("78.0", "°F"),
        "abc123_suction_pressure": ("193.0", "psi"),
        "abc123_suction_superheat": ("4.65234375", "°F"),
        "abc123_line_voltage": ("246", "V"),
    }
    for unique_id, (value, unit) in expected.items():
        state = hass.states.get(entity_id_for_unique_id(hass, "sensor", unique_id))
        assert state is not None, unique_id
        assert (state.state, state.attributes["unit_of_measurement"]) == (value, unit), unique_id


@pytest.mark.asyncio
async def test_unit_readings_stay_unavailable_until_carrier_reports_them(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Show an enabled outdoor reading as unavailable before any diagnostic message."""
    entry = await setup_integration()
    await _enable_and_reload(hass, entry, [("sensor", "abc123_line_voltage")])

    state = hass.states.get(entity_id_for_unique_id(hass, "sensor", "abc123_line_voltage"))

    assert state is not None
    assert state.state == STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_systems_without_an_outdoor_unit_get_no_outdoor_entities(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Skip outdoor readings and the lockout sensor for furnace-only systems."""
    carrier_api.systems[0].profile.outdoor_unit_type = "none"
    await setup_integration()
    registry = er.async_get(hass)

    for unique_id in OUTDOOR_SENSOR_IDS:
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, "abc123_outdoor_unit_lockout") is None
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_blower_speed")


@pytest.mark.asyncio
async def test_blower_speed_falls_back_to_the_system_status_field(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Read blower speed from the top-level status when the indoor unit lacks it."""
    carrier_api.systems[0].status.indoor_unit = None
    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, "sensor", "abc123_blower_speed"))

    assert state is not None
    assert state.state == "500"


@pytest.mark.asyncio
async def test_zone_damper_and_conditioning_sensors(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Expose damper position enabled and the raw conditioning value disabled by default."""
    entry = await setup_integration()

    damper = hass.states.get(
        entity_id_for_unique_id(hass, "sensor", "abc123_zone_1_damper_position")
    )
    assert damper is not None
    assert damper.state == "50"
    assert damper.attributes["friendly_name"] == "Home Living Room Damper Position"
    conditioning_id = entity_id_for_unique_id(hass, "sensor", "abc123_zone_1_conditioning")
    registry_entry = er.async_get(hass).async_get(conditioning_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    await _enable_and_reload(hass, entry, [("sensor", "abc123_zone_1_conditioning")])

    conditioning = hass.states.get(conditioning_id)
    assert conditioning is not None
    assert conditioning.state == "idle"
