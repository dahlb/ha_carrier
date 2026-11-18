"""Workflow tests for Carrier binary sensor platform registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.ha_carrier.binary_sensor import CarrierBinarySensor

from .conftest import FakeCarrierApiConnection, entity_id_for_unique_id


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lockout", "expected"), [(True, "on"), (False, "off"), (None, "unavailable")]
)
async def test_outdoor_unit_lockout_sensor(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    lockout: bool | None,
    expected: str,
) -> None:
    """Report a compressor lockout as a problem, once enabled and once Carrier sends it."""
    carrier_api.systems[0].status.outdoor_unit.lockout_active = lockout
    entry = await setup_integration()
    registry = er.async_get(hass)
    entity_id = entity_id_for_unique_id(hass, "binary_sensor", "abc123_outdoor_unit_lockout")
    registry_entry = registry.async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected
    assert state.attributes["device_class"] == "problem"
