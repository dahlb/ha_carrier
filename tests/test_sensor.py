"""Workflow tests for Carrier sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.ha_carrier.const import DOMAIN

from .conftest import FakeCarrierApiConnection, build_carrier_system, entity_id_for_unique_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unique_id", "expected_state"),
    [
        ("abc123_outdoor_temperature", "4.44444444444444"),
        ("abc123_filter_remaining", "80"),
        ("abc123_airflow", "1200"),
        ("abc123_static_pressure", "0.049817781666667"),
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
async def test_unit_status_sensors_use_carrier_api_status_unit_helpers(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Populate unit status attributes from mapped Carrier API status unit data."""
    carrier_api.systems = [build_carrier_system()]

    await setup_integration()

    expected_attributes: dict[str, dict[str, object]] = {
        "abc123_odu_status": {"operational_status": "idle"},
        "abc123_idu_status": {
            "airflow_cfm": 1200,
            "blower_rpm": 500,
            "operational_status": "idle",
            "static_pressure": 0.2,
        },
    }
    for unique_id, attributes in expected_attributes.items():
        entity_id = entity_id_for_unique_id(hass, "sensor", unique_id)
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == "idle"
        assert {key: state.attributes[key] for key in attributes} == attributes

    expected_states = {
        "abc123_airflow": "1200",
        "abc123_static_pressure": "0.049817781666667",
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
        "abc123_airflow": "1200",
        "abc123_static_pressure": "0.049817781666667",
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
        "abc123_airflow": "1200",
        "abc123_static_pressure": "0.049817781666667",
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
