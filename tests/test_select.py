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
import pytest

from custom_components.ha_carrier.const import HEAT_SOURCE_ODU_ONLY_LABEL

from .conftest import FakeCarrierApiConnection, entity_id_for_unique_id


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
