"""Workflow tests for Carrier config entry migration."""

from __future__ import annotations

from types import SimpleNamespace

from carrier_api import CarrierApiConnectionError
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_carrier import async_migrate_entry
from custom_components.ha_carrier.const import DOMAIN
from custom_components.ha_carrier.migrate import (
    _async_legacy_zone_name_from_entry,
    _async_migrate_entity_unique_ids,
    migrate_1_to_2,
    migrate_2_to_3,
)

from .conftest import (
    IDENTITY_ID,
    PASSWORD,
    USERNAME,
    FakeCarrierApiConnection,
    build_carrier_system,
)


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
    assert patch_carrier_api.cleanup_calls == 0


@pytest.mark.asyncio
async def test_migration_preserves_energy_and_propane_unique_ids(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Migrate enabled energy and propane sensor unique IDs to current suffixes."""
    system = patch_carrier_api.systems[0]
    system.config.fuel_type = "propane"
    system.config.gas_unit = "gallon"
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    for old_unique_id in (
        "ABC123_Heat Pump Heat Energy Yearly",
        "ABC123_hp_heat Energy Yesterday",
        "ABC123_hp_heat Energy Last Month",
        "ABC123_gas Energy Yearly",
        "ABC123_Propane Yearly",
        "ABC123_Propane Yearly Gallons",
        "ABC123_Heat Source",
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
        "abc123_gas_energy_year_to_date",
        "abc123_propane_usage_year_to_date",
        "abc123_propane_consumption_year_to_date",
        "abc123_heat_source",
    ):
        assert ent_reg.async_get_entity_id("sensor", DOMAIN, new_unique_id)
    assert patch_carrier_api.cleanup_calls == 0


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
    assert patch_carrier_api.cleanup_calls == 0


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
    assert patch_carrier_api.cleanup_calls == 0


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
    assert patch_carrier_api.cleanup_calls == 0


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


def _v1_entry(
    hass: HomeAssistant, *, version: int = 1, unique_id: str | None = None
) -> MockConfigEntry:
    """Add a legacy Carrier config entry to Home Assistant.

    Args:
        hass: Home Assistant instance.
        version: Config entry version.
        unique_id: Config entry unique ID.

    Returns:
        MockConfigEntry: The added config entry.
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=version,
        unique_id=unique_id,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    return config_entry


def _add_legacy_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, entities: list[tuple[str, str]]
) -> er.EntityRegistry:
    """Register legacy entities for a config entry.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry that owns the entities.
        entities: ``(domain, unique_id)`` pairs to register.

    Returns:
        er.EntityRegistry: The entity registry.
    """
    ent_reg = er.async_get(hass)
    for domain, unique_id in entities:
        ent_reg.async_get_or_create(domain, DOMAIN, unique_id, config_entry=config_entry)
    return ent_reg


@pytest.mark.asyncio
async def test_migration_maps_a_single_renamed_zone_to_its_api_id(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Pair the only unmatched legacy zone name with the only unmatched live zone."""
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(
        hass,
        config_entry,
        [
            ("climate", "ABC123_Old Den"),
            ("sensor", "ABC123_Old Den Humidity"),
            ("sensor", "ABC123_Old Den Temperature"),
            ("binary_sensor", "ABC123_Old Den Occupancy"),
        ],
    )

    assert await migrate_1_to_2(hass, config_entry)

    for domain, unique_id in [
        ("climate", "abc123_zone_1_thermostat"),
        ("sensor", "abc123_zone_1_humidity"),
        ("sensor", "abc123_zone_1_temperature"),
        ("binary_sensor", "abc123_zone_1_occupancy"),
    ]:
        assert ent_reg.async_get_entity_id(domain, DOMAIN, unique_id), unique_id


@pytest.mark.asyncio
async def test_migration_skips_zones_when_live_zone_names_are_duplicated(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Leave zone entities alone when two live zones share a name."""
    patch_carrier_api.systems = [
        build_carrier_system(second_zone_id="2", second_zone_name="Living Room")
    ]
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(hass, config_entry, [("climate", "ABC123_Living Room")])

    assert await migrate_1_to_2(hass, config_entry)

    assert ent_reg.async_get_entity_id("climate", DOMAIN, "ABC123_Living Room")


@pytest.mark.asyncio
async def test_migration_skips_zones_when_legacy_names_are_duplicated(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Leave zone entities alone when two legacy entries resolve to one zone name."""
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(
        hass,
        config_entry,
        [("sensor", "ABC123_Den Humidity"), ("binary_sensor", "ABC123_Den Humidity")],
    )

    assert await migrate_1_to_2(hass, config_entry)

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_Den Humidity")
    assert ent_reg.async_get_entity_id("binary_sensor", DOMAIN, "ABC123_Den Humidity")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_zone_id", "legacy_names"),
    [
        # Two unmatched legacy names but only one unmatched live zone.
        (None, ["Den", "Office"]),
        # Two unmatched legacy names and two unmatched live zones.
        ("2", ["Den", "Office"]),
    ],
)
async def test_migration_skips_ambiguous_unmatched_zones(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    second_zone_id: str | None,
    legacy_names: list[str],
) -> None:
    """Never guess a pairing when unmatched zones cannot be paired one-to-one."""
    patch_carrier_api.systems = [build_carrier_system(second_zone_id=second_zone_id)]
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(
        hass, config_entry, [("climate", f"ABC123_{name}") for name in legacy_names]
    )

    assert await migrate_1_to_2(hass, config_entry)

    for name in legacy_names:
        assert ent_reg.async_get_entity_id("climate", DOMAIN, f"ABC123_{name}")


@pytest.mark.asyncio
async def test_migration_removes_stale_and_duplicate_legacy_entities(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Remove legacy entities that v2 will not create, or that v2 already has."""
    patch_carrier_api.systems[0].config.uv_enabled = False
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(
        hass,
        config_entry,
        [
            ("sensor", "ABC123_UV Lamp Remaining"),
            ("sensor", "ABC123_Airflow"),
            ("sensor", "abc123_airflow"),
            ("sensor", "ABC123_Not A Carrier Entity"),
            ("sensor", "OTHER_Outdoor Temperature"),
        ],
    )

    assert await migrate_1_to_2(hass, config_entry)

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_UV Lamp Remaining") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_Airflow") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "abc123_airflow")
    # Entries that match nothing are kept for the user to clean up.
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_Not A Carrier Entity")
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "OTHER_Outdoor Temperature")


@pytest.mark.asyncio
@pytest.mark.parametrize("systems_loaded", [False, True])
async def test_migration_without_live_systems_keeps_stale_and_duplicate_entities(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    systems_loaded: bool,
) -> None:
    """Never delete registry entries when no live Carrier systems were loaded."""
    if systems_loaded:
        patch_carrier_api.systems = []
    else:
        patch_carrier_api.load_data_error = CarrierApiConnectionError("offline")
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(
        hass, config_entry, [("sensor", "ABC123_Airflow"), ("sensor", "abc123_airflow")]
    )

    assert await migrate_1_to_2(hass, config_entry)

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_Airflow")
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "abc123_airflow")
    assert config_entry.version == (2 if systems_loaded else 1)
    assert patch_carrier_api.cleanup_calls == 0


@pytest.mark.asyncio
async def test_migration_to_v3_only_bumps_version_when_already_keyed_by_identity(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Skip the identity lookup for entries already keyed by a non-username ID."""
    config_entry = _v1_entry(hass, version=2, unique_id=IDENTITY_ID)

    assert await migrate_2_to_3(hass, config_entry)

    assert config_entry.version == 3
    assert config_entry.unique_id == IDENTITY_ID
    assert ("get_user_info", {}) not in patch_carrier_api.calls


@pytest.mark.asyncio
async def test_migration_to_v3_never_closes_the_shared_session(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Use Home Assistant's shared session and never close it during migration."""
    config_entry = _v1_entry(hass, version=2, unique_id=USERNAME)

    assert await migrate_2_to_3(hass, config_entry)

    assert config_entry.version == 3
    assert config_entry.unique_id == IDENTITY_ID
    assert patch_carrier_api.client_session is async_get_clientsession(hass)
    assert patch_carrier_api.cleanup_calls == 0


@pytest.mark.parametrize(
    ("unique_id", "domain", "kind"),
    [
        # Belongs to another system.
        ("OTHER_Den Humidity", "sensor", "humidity"),
        # Does not end with the kind's display suffix.
        ("ABC123_Den Temperature", "sensor", "humidity"),
        # Thermostat kind, but not a climate entity.
        ("ABC123_Den", "sensor", "thermostat"),
    ],
)
def test_legacy_zone_name_is_none_for_non_matching_entries(
    unique_id: str, domain: str, kind: str
) -> None:
    """Return no legacy zone name for entries that do not fit the requested kind."""
    entry = SimpleNamespace(unique_id=unique_id, domain=domain)

    assert _async_legacy_zone_name_from_entry(entry, "ABC123", kind) is None


@pytest.mark.asyncio
async def test_non_destructive_migration_keeps_stale_and_duplicate_entries(
    hass: HomeAssistant,
) -> None:
    """Report, rather than delete, stale and duplicate entries when deletions are off."""
    config_entry = _v1_entry(hass)
    ent_reg = _add_legacy_entities(
        hass,
        config_entry,
        [
            ("sensor", "ABC123_Airflow"),
            ("sensor", "abc123_airflow"),
            ("sensor", "ABC123_UV Lamp Remaining"),
        ],
    )
    entries = list(ent_reg.entities.get_entries_for_config_entry_id(config_entry.entry_id))

    unmatched = _async_migrate_entity_unique_ids(
        ent_reg,
        entries,
        {
            "ABC123_Airflow": "abc123_airflow",
            "ABC123_UV Lamp Remaining": "abc123_uv_lamp_remaining",
            "abc123_airflow": "abc123_airflow",
        },
        {"abc123_airflow"},
        allow_deletions=False,
    )

    assert sorted(entry.unique_id for entry in unmatched) == [
        "ABC123_Airflow",
        "ABC123_UV Lamp Remaining",
    ]
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_Airflow")
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "ABC123_UV Lamp Remaining")
