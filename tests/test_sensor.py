"""Workflow tests for Carrier sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from .conftest import entity_id_for_unique_id


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
