"""Workflow tests for Carrier binary sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.ha_carrier.binary_sensor import CarrierBinarySensor

from .conftest import entity_id_for_unique_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "unique_id", "expected_state"),
    [
        ("binary_sensor", "abc123_online", "on"),
        ("binary_sensor", "abc123_humidifier_running", "on"),
        ("binary_sensor", "abc123_zone_1_occupancy", "on"),
    ],
)
async def test_binary_sensor_platform_registers_expected_entities(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    domain: str,
    unique_id: str,
    expected_state: str,
) -> None:
    """Register system and zone binary sensors with representative states."""
    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, domain, unique_id)
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == expected_state


@pytest.mark.asyncio
async def test_base_binary_sensor_requires_a_serial_and_defaults_to_unavailable(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Reject a missing serial and stay unavailable until a subclass reads data."""
    entry = await setup_integration()

    with pytest.raises(ValueError, match="serial is required"):
        CarrierBinarySensor("Test", entry.runtime_data, None)

    assert CarrierBinarySensor("Test", entry.runtime_data, "ABC123").available is False
