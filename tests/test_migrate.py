"""Workflow tests for Carrier config entry migration."""

from __future__ import annotations

from typing import Any, cast

from carrier_api import CarrierApiConnectionError
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_carrier import async_migrate_entry
from custom_components.ha_carrier.const import DOMAIN
from custom_components.ha_carrier.migrate import migrate_1_to_2, migrate_2_to_3

from .conftest import IDENTITY_ID, PASSWORD, USERNAME, FakeCarrierApiConnection


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
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
async def test_migration_preserves_energy_and_propane_unique_ids(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Migrate enabled energy and propane sensor unique IDs to current suffixes."""
    system = patch_carrier_api.systems[0]
    system.config.fuel_type = "propane"
    system.config.gas_unit = "gallon"
    cast("Any", system.energy).enabled_usage_metrics = lambda: ("hp_heat",)
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    for old_unique_id in (
        "ABC123_hp_heat Energy Yearly",
        "ABC123_hp_heat Energy Yesterday",
        "ABC123_hp_heat Energy Last Month",
        "ABC123_Propane Yearly",
        "ABC123_Propane Yearly Gallons",
    ):
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            old_unique_id,
            config_entry=config_entry,
        )

    assert await migrate_1_to_2(hass, config_entry)

    assert config_entry.version == 2
    for new_unique_id in (
        "abc123_hp_heat_energy_year_to_date",
        "abc123_hp_heat_energy_yesterday",
        "abc123_hp_heat_energy_last_month",
        "abc123_propane_usage_year_to_date",
        "abc123_propane_consumption_year_to_date",
    ):
        assert ent_reg.async_get_entity_id("sensor", DOMAIN, new_unique_id)
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "load_error",
    [
        CarrierApiConnectionError("offline"),
    ],
)
async def test_migration_defers_version_update_when_live_data_cannot_load(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    load_error: BaseException,
) -> None:
    """Keep migration non-destructive when Carrier data cannot be loaded."""
    patch_carrier_api.load_data_error = load_error
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
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
async def test_migration_updates_config_entry_unique_id_to_identity_id(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Migrate v2 username-keyed config entries to Carrier identity IDs."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)

    assert await migrate_2_to_3(hass, config_entry)

    assert config_entry.version == 3
    assert config_entry.unique_id == IDENTITY_ID
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "load_error",
    [
        CarrierApiConnectionError("offline"),
    ],
)
async def test_migration_defers_identity_update_when_user_info_cannot_load(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    load_error: BaseException,
) -> None:
    """Keep v2 entries unchanged when Carrier identity lookup fails."""
    patch_carrier_api.load_data_error = load_error
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)

    assert await migrate_2_to_3(hass, config_entry)

    assert config_entry.version == 2
    assert config_entry.unique_id == USERNAME
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
async def test_migration_defers_identity_update_when_identity_id_is_missing(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Keep v2 entries unchanged when Carrier omits identityId."""
    patch_carrier_api.identity_id = ""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)

    assert await migrate_2_to_3(hass, config_entry)

    assert config_entry.version == 2
    assert config_entry.unique_id == USERNAME


@pytest.mark.asyncio
async def test_migration_rejects_conflicting_identity_id(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Reject v2 migration when another entry already owns the identity ID."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    conflicting_entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id=IDENTITY_ID,
        data={CONF_USERNAME: "other@example.com", CONF_PASSWORD: PASSWORD},
    )
    conflicting_entry.add_to_hass(hass)

    assert not await migrate_2_to_3(hass, config_entry)

    assert config_entry.version == 2
    assert config_entry.unique_id == USERNAME


@pytest.mark.asyncio
async def test_migration_chains_v1_to_v3(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Run v1 config entries through entity and identity migrations."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, config_entry)

    assert config_entry.version == 3
    assert config_entry.unique_id == IDENTITY_ID
