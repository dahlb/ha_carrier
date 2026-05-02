"""Config entry migration helpers for the Carrier integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging

from aiohttp import ClientError
from carrier_api import ApiConnectionGraphql, AuthError, BaseError, System
from gql.transport.exceptions import TransportError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import EntityRegistry, RegistryEntry
from homeassistant.util import slugify

from .const import CONFIG_FLOW_VERSION
from .util import ENERGY_METRIC_MAP, TIMESTAMP_TYPES, has_heat

_LOGGER: logging.Logger = logging.getLogger(__name__)

ALWAYS_CREATED_SYSTEM_ENTITY_SUFFIXES: tuple[str, ...] = (
    "Online",
    "Outdoor Temperature",
    "Filter Remaining",
    "Airflow",
    "Static Pressure",
    "ODU Status",
    "IDU Status",
)
CONDITIONALLY_CREATED_SYSTEM_ENTITY_SUFFIXES: tuple[str, ...] = (
    "Humidifier Running",
    "Heat Source",
    "Humidifier Remaining",
    "UV Lamp Remaining",
    "ODU Var",
)
ZONE_ENTITY_SUFFIXES: dict[str, str] = {
    "thermostat": "",
    "occupancy": " Occupancy",
    "humidity": " Humidity",
    "temperature": " Temperature",
}
SYSTEM_ENTITY_SUFFIXES: set[str] = {
    *ALWAYS_CREATED_SYSTEM_ENTITY_SUFFIXES,
    *CONDITIONALLY_CREATED_SYSTEM_ENTITY_SUFFIXES,
}


def _async_new_unique_id(system_serial: str, new_suffix: str) -> str:
    """Return the version 2 unique ID for a Carrier entity.

    Args:
        system_serial: Carrier system serial number.
        new_suffix: Version 2 unique ID suffix.

    Returns:
        str: Slugified version 2 unique ID.
    """
    return slugify(f"{system_serial}_{new_suffix}")


def _async_add_unique_id_migration(
    migration_map: dict[str, str],
    system_serial: str,
    old_suffix: str,
    new_suffix: str | None = None,
) -> None:
    """Add one legacy unique ID to version 2 unique ID mapping.

    Args:
        migration_map: Mutable migration map being built.
        system_serial: Carrier system serial number.
        old_suffix: Version 1 unique ID suffix.
        new_suffix: Version 2 unique ID suffix. Defaults to old_suffix.

    Returns:
        None: The mapping is updated in place.
    """
    migration_map[f"{system_serial}_{old_suffix}"] = _async_new_unique_id(
        system_serial, new_suffix or old_suffix
    )


def _async_old_unique_id_suffix(entry: RegistryEntry, system_serial: str) -> str | None:
    """Return the v1 unique ID suffix for an entity registry entry.

    Args:
        entry: Entity registry entry being considered for migration.
        system_serial: Carrier system serial number expected in the unique ID.

    Returns:
        str | None: Legacy suffix after the system serial prefix, or None when
            the entry does not belong to the system serial.
    """
    unique_id_prefix = f"{system_serial}_"
    if not entry.unique_id.startswith(unique_id_prefix):
        return None
    return entry.unique_id.removeprefix(unique_id_prefix)


def _async_zone_entry_kind(entry: RegistryEntry, system_serial: str) -> str | None:
    """Return the zone entity kind represented by a v1 registry entry.

    Args:
        entry: Entity registry entry being considered for migration.
        system_serial: Carrier system serial number expected in the unique ID.

    Returns:
        str | None: Zone entity kind such as ``thermostat`` or ``humidity``.
    """
    old_suffix = _async_old_unique_id_suffix(entry, system_serial)
    if old_suffix is None:
        return None

    # Climate entries are zone thermostats even when the zone name itself ends
    # with a sensor-like suffix such as " Humidity" or " Temperature".
    if entry.domain == "climate":
        return "thermostat"

    # Several system-level entities also end with zone-like words. Keep them out
    # of zone migration classification so "Outdoor Temperature" is not treated
    # as a zone temperature entity.
    if old_suffix in SYSTEM_ENTITY_SUFFIXES:
        return None

    for kind, display_suffix in ZONE_ENTITY_SUFFIXES.items():
        if display_suffix and old_suffix.endswith(display_suffix):
            return kind

    return None


def _async_add_zone_unique_id_migration(
    migration_map: dict[str, str],
    system_serial: str,
    legacy_zone_name: str,
    zone_api_id: str,
    kind: str,
) -> None:
    """Add one legacy zone unique ID to version 2 unique ID mapping.

    Args:
        migration_map: Mutable migration map being built.
        system_serial: Carrier system serial number.
        legacy_zone_name: Zone name stored in the v1 unique ID.
        zone_api_id: Stable Carrier zone API identifier.
        kind: Zone entity kind being migrated.

    Returns:
        None: The mapping is updated in place.
    """
    display_suffix = ZONE_ENTITY_SUFFIXES[kind]
    _async_add_unique_id_migration(
        migration_map,
        system_serial,
        f"{legacy_zone_name}{display_suffix}",
        f"zone_{zone_api_id}_{kind}",
    )


def _async_legacy_zone_name_from_entry(
    entry: RegistryEntry,
    system_serial: str,
    kind: str,
) -> str | None:
    """Return the legacy zone name stored in a v1 registry entry.

    Args:
        entry: Entity registry entry being considered for migration.
        system_serial: Carrier system serial number expected in the unique ID.
        kind: Zone entity kind being migrated.

    Returns:
        str | None: Legacy zone name, or None when the entry does not match.
    """
    old_suffix = _async_old_unique_id_suffix(entry, system_serial)
    if old_suffix is None:
        return None

    display_suffix = ZONE_ENTITY_SUFFIXES[kind]
    if display_suffix:
        if not old_suffix.endswith(display_suffix):
            return None
        return old_suffix[: -len(display_suffix)]

    if _async_zone_entry_kind(entry, system_serial) == "thermostat":
        return old_suffix
    return None


def _async_build_registry_zone_migration_map(
    systems: Iterable[System],
    registry_entries: Iterable[RegistryEntry],
) -> dict[str, str]:
    """Build zone unique ID mappings from existing registry entries.

    Args:
        systems: Carrier systems loaded from the account during migration.
        registry_entries: Existing entity registry entries for the config entry.

    Returns:
        dict[str, str]: Registry-derived v1 to v2 zone unique ID mappings.
    """
    migration_map: dict[str, str] = {}
    entries = list(registry_entries)

    for carrier_system in systems:
        system_serial = carrier_system.profile.serial
        zones = list(carrier_system.config.zones)

        for kind in ZONE_ENTITY_SUFFIXES:
            # v1 zone unique IDs used the display name stored in the registry.
            # Build the old side from registry entries instead of current
            # Carrier zone names so user-renamed zones still migrate.
            matched_entries = [
                entry for entry in entries if _async_zone_entry_kind(entry, system_serial) == kind
            ]
            legacy_name_to_entry: dict[str, RegistryEntry] = {}
            for entry in matched_entries:
                legacy_zone_name = _async_legacy_zone_name_from_entry(entry, system_serial, kind)
                if legacy_zone_name is None:
                    continue
                if legacy_zone_name in legacy_name_to_entry:
                    _LOGGER.warning(
                        "Skipping ambiguous Carrier %s zone migration for %s: "
                        "duplicate legacy zone name %s",
                        kind,
                        system_serial,
                        legacy_zone_name,
                    )
                    legacy_name_to_entry = {}
                    break
                legacy_name_to_entry[legacy_zone_name] = entry

            if not legacy_name_to_entry:
                continue

            # Current live data is only used to resolve the target zone API ID.
            # Duplicate names make that association unsafe, so skip instead of
            # guessing and possibly moving an entity to the wrong zone.
            zone_name_to_zone = {zone.name: zone for zone in zones}
            if len(zone_name_to_zone) != len(zones):
                _LOGGER.warning(
                    "Skipping ambiguous Carrier %s zone migration for %s: duplicate zone names",
                    kind,
                    system_serial,
                )
                continue

            for legacy_zone_name, zone in zone_name_to_zone.items():
                if legacy_zone_name not in legacy_name_to_entry:
                    continue
                # Exact name match: the common case, and the safest mapping.
                _async_add_zone_unique_id_migration(
                    migration_map,
                    system_serial,
                    legacy_zone_name,
                    zone.api_id,
                    kind,
                )

            unmatched_zones = [zone for zone in zones if zone.name not in legacy_name_to_entry]
            unmatched_legacy_names = [
                legacy_zone_name
                for legacy_zone_name in legacy_name_to_entry
                if legacy_zone_name not in zone_name_to_zone
            ]

            if len(unmatched_legacy_names) != len(unmatched_zones):
                # Different counts means no one-to-one fallback is possible.
                if unmatched_legacy_names or unmatched_zones:
                    _LOGGER.warning(
                        "Skipping ambiguous Carrier %s zone migration for %s: "
                        "%s unmatched entries, %s unmatched zones",
                        kind,
                        system_serial,
                        len(unmatched_legacy_names),
                        len(unmatched_zones),
                    )
                continue

            if len(unmatched_legacy_names) == 1 and len(unmatched_zones) == 1:
                # One old zone and one live zone remain unmatched. Treat this as
                # the only safe rename fallback.
                legacy_zone_name = unmatched_legacy_names[0]
                zone = unmatched_zones[0]
                _async_add_zone_unique_id_migration(
                    migration_map,
                    system_serial,
                    legacy_zone_name,
                    zone.api_id,
                    kind,
                )
                continue

            if unmatched_legacy_names or unmatched_zones:
                # Multiple unmatched zones could be reordered or renamed; do not
                # pair by position because that can assign the wrong zone API ID.
                _LOGGER.warning(
                    "Skipping ambiguous Carrier %s zone migration for %s: "
                    "legacy zone names do not uniquely match current zone names",
                    kind,
                    system_serial,
                )

    return migration_map


def _async_build_unique_id_migration_map(
    systems: Iterable[System],
    registry_entries: Iterable[RegistryEntry],
) -> dict[str, str]:
    """Build old-to-new entity unique ID mappings for Carrier systems.

    Args:
        systems: Carrier systems loaded from the account during migration.
        registry_entries: Existing entity registry entries for the config entry.

    Returns:
        dict[str, str]: Mapping from version 1 unique IDs to version 2 unique IDs.
    """
    migration_map: dict[str, str] = {}

    for carrier_system in systems:
        system_serial = carrier_system.profile.serial

        # System-level v1 unique IDs only changed by slugification and, for a
        # few entities, display suffix changes. These do not depend on zone API
        # IDs, so they can be mapped directly from live system data.
        for suffix in (
            *ALWAYS_CREATED_SYSTEM_ENTITY_SUFFIXES,
            *CONDITIONALLY_CREATED_SYSTEM_ENTITY_SUFFIXES,
        ):
            _async_add_unique_id_migration(migration_map, system_serial, suffix)

        for timestamp_type in TIMESTAMP_TYPES:
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"updated {timestamp_type.replace('_', ' ').capitalize()} at",
                f"{timestamp_type.replace('_', ' ').title()} Last Updated",
            )

        fuel_type = carrier_system.config.fuel_type
        _async_add_unique_id_migration(
            migration_map,
            system_serial,
            f"{fuel_type.capitalize()} Yearly",
            f"{fuel_type.capitalize()} Usage Year to Date",
        )
        _async_add_unique_id_migration(
            migration_map,
            system_serial,
            "Propane Yearly Gallons",
            "Propane Consumption Year to Date",
        )

        for metric in ENERGY_METRIC_MAP:
            metric_title = metric.replace("_", " ").title()
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"{metric} Energy Yearly",
                f"{metric_title} Energy Year to Date",
            )
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"{metric} Energy Yesterday",
                f"{metric_title} Energy Yesterday",
            )
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"{metric} Energy Last Month",
                f"{metric_title} Energy Last Month",
            )

    # Zone entities need registry context because their v1 unique IDs were based
    # on names and v2 unique IDs are based on stable Carrier zone API IDs.
    migration_map.update(_async_build_registry_zone_migration_map(systems, registry_entries))
    return migration_map


def _async_build_created_unique_ids(systems: Iterable[System]) -> set[str]:
    """Build unique IDs that version 2 setup will create for Carrier systems.

    Args:
        systems: Carrier systems loaded from the account during migration.

    Returns:
        set[str]: Version 2 entity unique IDs expected to be created.
    """
    created_unique_ids: set[str] = set()

    for carrier_system in systems:
        system_serial = carrier_system.profile.serial
        for suffix in ALWAYS_CREATED_SYSTEM_ENTITY_SUFFIXES:
            created_unique_ids.add(_async_new_unique_id(system_serial, suffix))

        for timestamp_type in TIMESTAMP_TYPES:
            created_unique_ids.add(
                _async_new_unique_id(
                    system_serial,
                    f"{timestamp_type.replace('_', ' ').title()} Last Updated",
                )
            )

        if carrier_system.config.humidifier_enabled:
            created_unique_ids.add(_async_new_unique_id(system_serial, "Humidifier Running"))
            created_unique_ids.add(_async_new_unique_id(system_serial, "Humidifier Remaining"))
        if carrier_system.config.uv_enabled:
            created_unique_ids.add(_async_new_unique_id(system_serial, "UV Lamp Remaining"))
        if carrier_system.profile.outdoor_unit_type in ["varcaphp", "varcapac"]:
            created_unique_ids.add(_async_new_unique_id(system_serial, "ODU Var"))
        if has_heat(carrier_system):
            created_unique_ids.add(_async_new_unique_id(system_serial, "Heat Source"))

        for metric in ENERGY_METRIC_MAP:
            if getattr(carrier_system.energy, metric, False) is True:
                metric_title = metric.replace("_", " ").title()
                created_unique_ids.add(
                    _async_new_unique_id(system_serial, f"{metric_title} Energy Year to Date")
                )
                created_unique_ids.add(
                    _async_new_unique_id(system_serial, f"{metric_title} Energy Yesterday")
                )
                created_unique_ids.add(
                    _async_new_unique_id(system_serial, f"{metric_title} Energy Last Month")
                )

        if getattr(carrier_system.energy, "gas", False) is True:
            created_unique_ids.add(
                _async_new_unique_id(
                    system_serial,
                    f"{carrier_system.config.fuel_type.capitalize()} Usage Year to Date",
                )
            )
            if carrier_system.config.fuel_type == "propane":
                created_unique_ids.add(
                    _async_new_unique_id(system_serial, "Propane Consumption Year to Date")
                )

        for zone in carrier_system.config.zones:
            zone_unique_id_suffix = f"zone_{zone.api_id}"
            created_unique_ids.add(
                _async_new_unique_id(system_serial, f"{zone_unique_id_suffix}_thermostat")
            )
            created_unique_ids.add(
                _async_new_unique_id(system_serial, f"{zone_unique_id_suffix}_humidity")
            )
            created_unique_ids.add(
                _async_new_unique_id(system_serial, f"{zone_unique_id_suffix}_temperature")
            )
            if zone.occupancy_enabled:
                created_unique_ids.add(
                    _async_new_unique_id(system_serial, f"{zone_unique_id_suffix}_occupancy")
                )

    return created_unique_ids


def _async_migrate_entity_unique_ids(
    ent_reg: EntityRegistry,
    registry_entries: Iterable[RegistryEntry],
    migration_map: Mapping[str, str],
    created_unique_ids: set[str],
    allow_deletions: bool,
) -> list[RegistryEntry]:
    """Rename or remove legacy Carrier entity registry entries.

    Args:
        ent_reg: Home Assistant entity registry.
        registry_entries: Entity registry entries owned by the config entry.
        migration_map: Old-to-new unique ID migration map.
        created_unique_ids: Unique IDs that version 2 setup will create.
        allow_deletions: Whether stale entries may be removed.

    Returns:
        list[RegistryEntry]: Registry entries that could not be matched and updated.
    """
    existing_unique_ids = {entry.unique_id for entry in registry_entries}
    unmatched_entries: list[RegistryEntry] = []

    for entry in registry_entries:
        new_unique_id = migration_map.get(entry.unique_id)
        if new_unique_id == entry.unique_id:
            continue

        if new_unique_id is None:
            unmatched_entries.append(entry)
            continue

        if new_unique_id not in created_unique_ids:
            # Only remove stale entities when live discovery succeeded. If the
            # Carrier API was unavailable, created_unique_ids is incomplete.
            if allow_deletions:
                _LOGGER.info(
                    "Removing stale Carrier entity %s during unique ID migration",
                    entry.entity_id,
                )
                ent_reg.async_remove(entry.entity_id)
                existing_unique_ids.discard(entry.unique_id)
            else:
                unmatched_entries.append(entry)
            continue

        if new_unique_id in existing_unique_ids:
            # A current v2 entry already exists. Remove only the old duplicate,
            # and only when live data proved the new entity should exist.
            if allow_deletions:
                _LOGGER.info(
                    "Removing duplicate legacy Carrier entity %s during unique ID migration",
                    entry.entity_id,
                )
                ent_reg.async_remove(entry.entity_id)
                existing_unique_ids.discard(entry.unique_id)
            else:
                unmatched_entries.append(entry)
            continue

        ent_reg.async_update_entity(entry.entity_id, new_unique_id=new_unique_id)
        existing_unique_ids.discard(entry.unique_id)
        existing_unique_ids.add(new_unique_id)

    return unmatched_entries


async def _async_migrate_entity_registry_unique_ids(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    systems: Iterable[System],
    allow_deletions: bool,
) -> list[RegistryEntry]:
    """Migrate or remove entity registry entries for one Carrier config entry.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier config entry being migrated.
        systems: Carrier systems loaded from the account during migration.
        allow_deletions: Whether stale entries may be removed.

    Returns:
        list[RegistryEntry]: Registry entries that could not be matched and updated.
    """
    ent_reg = er.async_get(hass)
    registry_entries = list(ent_reg.entities.get_entries_for_config_entry_id(config_entry.entry_id))
    return _async_migrate_entity_unique_ids(
        ent_reg,
        registry_entries,
        _async_build_unique_id_migration_map(systems, registry_entries),
        _async_build_created_unique_ids(systems),
        allow_deletions,
    )


async def migrate_1_to_2(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate a Carrier config entry from version 1 to version 2.

    Args:
        hass: Home Assistant instance.
        config_entry: Version 1 Carrier config entry being migrated.

    Returns:
        bool: True when entity registry migration and config entry version
            update succeed.
    """
    systems_loaded = False
    try:
        api_connection = ApiConnectionGraphql(
            username=config_entry.data[CONF_USERNAME],
            password=config_entry.data[CONF_PASSWORD],
        )
        systems = await api_connection.load_data()
        systems_loaded = True
    except (
        AuthError,
        BaseError,
        ClientError,
        TimeoutError,
        OSError,
        TransportError,
    ):
        _LOGGER.warning(
            "Unable to load Carrier data for config entry migration; "
            "continuing without destructive entity registry cleanup",
            exc_info=True,
        )
        systems = []

    if systems_loaded and not systems:
        _LOGGER.warning(
            "No Carrier systems loaded for config entry migration; "
            "running non-destructive registry migration only"
        )

    # Missing live data still lets the migration attempt safe registry updates,
    # but it must not delete stale entries or mark the config entry as migrated.
    unmatched_entries = await _async_migrate_entity_registry_unique_ids(
        hass,
        config_entry,
        systems,
        bool(systems),
    )
    if unmatched_entries:
        _LOGGER.info(
            "Carrier config entry migration could not match and update entities: %s",
            [entry.entity_id for entry in unmatched_entries],
        )
    if systems_loaded:
        hass.config_entries.async_update_entry(config_entry, version=CONFIG_FLOW_VERSION)
        _LOGGER.info("Carrier config entry migration to version %s complete", CONFIG_FLOW_VERSION)
    else:
        _LOGGER.warning(
            "Carrier migration deferred due to data-load failure; will retry on next startup"
        )
    return True
