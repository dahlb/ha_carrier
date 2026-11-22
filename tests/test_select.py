"""Workflow tests for Carrier select entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.ha_carrier.const import (
    HEAT_SOURCE_IDU_ONLY_LABEL,
    HEAT_SOURCE_ODU_ONLY_LABEL,
    HEAT_SOURCE_SYSTEM_LABEL,
)
from custom_components.ha_carrier.select import CarrierSelect

from .conftest import (
    FakeCarrierApiConnection,
    build_carrier_system,
    entity_for_unique_id,
    entity_id_for_unique_id,
)


@pytest.mark.asyncio
async def test_select_platform_registers_heat_source_select(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Register a heat-source select entity for heat-capable systems."""
    await setup_integration()

    entity_id = entity_id_for_unique_id(hass, SELECT_DOMAIN, "abc123_heat_source")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == "system in control"
    assert HEAT_SOURCE_ODU_ONLY_LABEL in state.attributes["options"]


@pytest.mark.asyncio
async def test_select_option_calls_carrier_api_and_updates_local_state(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Write a selected heat source through Home Assistant services."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, SELECT_DOMAIN, "abc123_heat_source")

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: HEAT_SOURCE_ODU_ONLY_LABEL},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert carrier_api.calls[-1][0] == "set_heat_source"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HEAT_SOURCE_ODU_ONLY_LABEL


@pytest.mark.asyncio
async def test_heat_source_select_without_a_heat_pump_or_known_fuel(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Offer only indoor-unit and system options, labelled generically without a fuel."""
    system = build_carrier_system(has_heat_pump=False)
    system.profile.indoor_unit_source = None
    carrier_api.systems = [system]
    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, SELECT_DOMAIN, "abc123_heat_source"))

    assert state is not None
    assert state.attributes["options"] == [HEAT_SOURCE_IDU_ONLY_LABEL, HEAT_SOURCE_SYSTEM_LABEL]


@pytest.mark.asyncio
async def test_heat_source_select_rejects_an_unknown_option(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Raise a translated validation error for an option outside the list."""
    await setup_integration()
    entity = entity_for_unique_id(hass, SELECT_DOMAIN, "abc123_heat_source")

    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_select_option("solar")

    assert raised.value.translation_key == "unsupported_heat_source"


@pytest.mark.asyncio
async def test_base_select_defaults_to_unavailable(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Stay unavailable until a subclass reads data."""
    entry = await setup_integration()

    assert CarrierSelect("Test", entry.runtime_data, "ABC123").available is False
