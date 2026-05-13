"""Workflow tests for Carrier sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

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
