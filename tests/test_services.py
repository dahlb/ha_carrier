"""Tests for the Carrier integration's service action registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import probatio
import pytest

from custom_components.ha_carrier.const import DOMAIN
from custom_components.ha_carrier.services import (
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_ACTIVITY_SETPOINT,
    SERVICE_SET_PROGRAM_DAY,
)

from .conftest import FakeCarrierApiConnection, entity_id_for_unique_id

SERVICES = (SERVICE_SET_ACTIVITY_SETPOINT, SERVICE_GET_SCHEDULE, SERVICE_SET_PROGRAM_DAY)


@pytest.mark.asyncio
async def test_services_register_without_a_config_entry(hass: HomeAssistant) -> None:
    """Register every service action in async_setup, before any entry loads."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)


@pytest.mark.asyncio
async def test_services_stay_registered_after_entry_unload(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Keep service actions registered after the config entry unloads."""
    entry = await setup_integration()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)


@pytest.mark.asyncio
async def test_get_schedule_returns_response_for_a_zone_thermostat(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
) -> None:
    """Dispatch a registered action to the targeted Carrier climate entity."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )

    assert entity_id in response


@pytest.mark.asyncio
async def test_set_activity_setpoint_rejects_unknown_activity(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Reject an activity outside the service schema before reaching the entity."""
    await setup_integration()
    entity_id = entity_id_for_unique_id(hass, CLIMATE_DOMAIN, "abc123_zone_1_thermostat")

    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ACTIVITY_SETPOINT,
            {ATTR_ENTITY_ID: entity_id, "temperature": 70, "activity": "vacation"},
            blocking=True,
        )
