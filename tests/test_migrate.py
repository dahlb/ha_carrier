"""Workflow tests for Carrier config entry migration."""

from __future__ import annotations

from aiohttp import ClientError
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_carrier.const import DOMAIN
from custom_components.ha_carrier.migrate import migrate_1_to_2

from .conftest import PASSWORD, USERNAME, FakeCarrierApiConnection


@pytest.mark.asyncio
async def test_migration_updates_system_and_zone_unique_ids(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Migrate legacy v1 entity registry IDs to v2 unique IDs."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "ABC123_Outdoor Temperature",
        config_entry=config_entry,
    )
    ent_reg.async_get_or_create(
        "climate",
        DOMAIN,
        "ABC123_Living Room",
        config_entry=config_entry,
    )

    assert await migrate_1_to_2(hass, config_entry)

    assert config_entry.version == 2
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "abc123_outdoor_temperature")
    assert ent_reg.async_get_entity_id("climate", DOMAIN, "abc123_zone_1_thermostat")


@pytest.mark.asyncio
async def test_migration_defers_version_update_when_live_data_cannot_load(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Keep migration non-destructive when Carrier data cannot be loaded."""
    patch_carrier_api.load_data_error = ClientError("offline")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "ABC123_Unknown Legacy Entity",
        config_entry=config_entry,
    )

    assert await migrate_1_to_2(hass, config_entry)

    assert config_entry.version == 1
    assert ent_reg.async_get(entry.entity_id) is not None
