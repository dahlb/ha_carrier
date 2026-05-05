"""Workflow tests for Carrier binary sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

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
