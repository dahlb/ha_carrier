"""Initialize and manage the Home Assistant Carrier integration lifecycle."""

import asyncio
from collections.abc import Iterable, Mapping
import logging

from aiohttp import ClientError
from carrier_api import ApiConnectionGraphql, AuthError, BaseError, System
from gql.transport.exceptions import TransportError, TransportServerError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.entity_registry import EntityRegistry, RegistryEntry
from homeassistant.util import slugify

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator, CarrierUnauthorizedError
from .const import (
    CONFIG_FLOW_VERSION,
    DOMAIN,
    PLATFORMS,
    TO_REDACT,
    WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS,
    WEBSOCKET_RETRY_MAX_DELAY_SECONDS,
)
from .util import TIMESTAMP_TYPES, async_redact_data, has_heat

type ConfigEntryCarrier = ConfigEntry[CarrierDataUpdateCoordinator]

_LOGGER: logging.Logger = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# For migrations from version 1 to 2
ENERGY_METRICS: tuple[str, ...] = (
    "cooling",
    "hp_heat",
    "fan",
    "electric_heat",
    "reheat",
    "fan_gas",
    "loop_pump",
)
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


def _async_build_unique_id_migration_map(systems: Iterable[System]) -> dict[str, str]:
    """Build old-to-new entity unique ID mappings for Carrier systems.

    Args:
        systems: Carrier systems loaded from the account during migration.

    Returns:
        dict[str, str]: Mapping from version 1 unique IDs to version 2 unique IDs.
    """
    migration_map: dict[str, str] = {}

    for carrier_system in systems:
        system_serial = carrier_system.profile.serial

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

        for metric in ENERGY_METRICS:
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

        for zone in carrier_system.config.zones:
            zone_unique_id_suffix = f"zone_{zone.api_id}"
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                zone.name,
                f"{zone_unique_id_suffix}_thermostat",
            )
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"{zone.name} Occupancy",
                f"{zone_unique_id_suffix}_occupancy",
            )
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"{zone.name} Humidity",
                f"{zone_unique_id_suffix}_humidity",
            )
            _async_add_unique_id_migration(
                migration_map,
                system_serial,
                f"{zone.name} Temperature",
                f"{zone_unique_id_suffix}_temperature",
            )

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

        for metric in ENERGY_METRICS:
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
) -> None:
    """Rename or remove legacy Carrier entity registry entries.

    Args:
        ent_reg: Home Assistant entity registry.
        registry_entries: Entity registry entries owned by the config entry.
        migration_map: Old-to-new unique ID migration map.
        created_unique_ids: Unique IDs that version 2 setup will create.

    Returns:
        None: Entity registry entries are updated in place.
    """
    existing_unique_ids = {entry.unique_id for entry in registry_entries}

    for entry in registry_entries:
        new_unique_id = migration_map.get(entry.unique_id)
        if new_unique_id is None or new_unique_id == entry.unique_id:
            continue

        if new_unique_id not in created_unique_ids:
            _LOGGER.debug(
                "Removing stale Carrier entity %s during unique ID migration",
                entry.entity_id,
            )
            ent_reg.async_remove(entry.entity_id)
            existing_unique_ids.discard(entry.unique_id)
            continue

        if new_unique_id in existing_unique_ids:
            _LOGGER.debug(
                "Removing duplicate legacy Carrier entity %s during unique ID migration",
                entry.entity_id,
            )
            ent_reg.async_remove(entry.entity_id)
            existing_unique_ids.discard(entry.unique_id)
            continue

        ent_reg.async_update_entity(entry.entity_id, new_unique_id=new_unique_id)
        existing_unique_ids.discard(entry.unique_id)
        existing_unique_ids.add(new_unique_id)


async def _async_migrate_entity_registry_unique_ids(
    hass: HomeAssistant,
    config_entry: ConfigEntryCarrier,
    migration_map: Mapping[str, str],
    created_unique_ids: set[str],
) -> None:
    """Migrate or remove entity registry entries for one Carrier config entry.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier config entry being migrated.
        migration_map: Old-to-new unique ID migration map.
        created_unique_ids: Unique IDs that version 2 setup will create.

    Returns:
        None: Entity registry entries are updated in place.
    """
    ent_reg = er.async_get(hass)
    registry_entries = list(ent_reg.entities.get_entries_for_config_entry_id(config_entry.entry_id))
    _async_migrate_entity_unique_ids(
        ent_reg,
        registry_entries,
        migration_map,
        created_unique_ids,
    )


async def _migrate_1_to_2(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> bool:
    """Migrate a Carrier config entry from version 1 to version 2.

    Args:
        hass: Home Assistant instance.
        config_entry: Version 1 Carrier config entry being migrated.

    Returns:
        bool: True when entity registry migration and config entry version
            update succeed.
    """
    try:
        api_connection = ApiConnectionGraphql(
            username=config_entry.data[CONF_USERNAME],
            password=config_entry.data[CONF_PASSWORD],
        )
        systems = await api_connection.load_data()
    except (
        AuthError,
        BaseError,
        ClientError,
        TimeoutError,
        OSError,
        TransportError,
    ):
        _LOGGER.exception("Unable to load Carrier data for config entry migration")
        return False

    await _async_migrate_entity_registry_unique_ids(
        hass,
        config_entry,
        _async_build_unique_id_migration_map(systems),
        _async_build_created_unique_ids(systems),
    )
    hass.config_entries.async_update_entry(config_entry, version=CONFIG_FLOW_VERSION)
    _LOGGER.info("Carrier config entry migration to version %s complete", CONFIG_FLOW_VERSION)
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> bool:
    """Migrate Carrier config entries between config flow versions.

    Args:
        hass: Home Assistant instance.
        config_entry: Configuration entry being migrated.

    Returns:
        bool: True when migration succeeds.
    """
    _LOGGER.debug(
        "Migrating Carrier config entry from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version == CONFIG_FLOW_VERSION:
        return True

    if config_entry.version != 1:
        _LOGGER.error(
            "Unable to migrate Carrier config entry from version %s", config_entry.version
        )
        return False

    if config_entry.version == 1:
        return await _migrate_1_to_2(hass=hass, config_entry=config_entry)

    if config_entry.version == 2:
        return True
    return False


async def _async_await_websocket_task(websocket_task: asyncio.Task[None]) -> None:
    """Await websocket task shutdown after cancellation.

    Args:
        websocket_task: Background websocket listener task to await.

    Returns:
        None: The task is fully drained before unload continues.
    """
    try:
        await websocket_task
    except asyncio.CancelledError:
        pass
    except (
        CarrierUnauthorizedError,
        ClientError,
        TimeoutError,
        OSError,
        TransportError,
    ):
        _LOGGER.exception("websocket task raised during cancellation")


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> bool:
    """Set up one Carrier config entry and start platform forwarding.

    The setup creates a Carrier API connection, initializes the data
    coordinator, performs the first refresh, and starts a long-running
    websocket listener task for near-real-time updates.

    Args:
        hass: Home Assistant instance.
        config_entry: Configuration entry containing Carrier credentials.

    Returns:
        bool: True when setup succeeds.

    Raises:
        ConfigEntryNotReady: Raised when authentication or initial data loading
            fails and Home Assistant should retry setup later.
        CarrierUnauthorizedError: Raised when repeated unauthorized responses
            indicate invalid credentials rather than a transient outage.
    """
    _LOGGER.debug(
        "async setup entry: %s",
        async_redact_data(config_entry.as_dict(), TO_REDACT),
    )
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]

    try:
        api_connection = ApiConnectionGraphql(username=username, password=password)
        coordinator = CarrierDataUpdateCoordinator(
            hass=hass,
            api_connection=api_connection,
        )
        await coordinator.async_config_entry_first_refresh()
        config_entry.runtime_data = coordinator

        async def ws_updates() -> None:
            """Keep websocket updates running for this config entry.

            The loop exits on cancellation and forces a coordinator refresh if
            websocket handling fails so entity state can recover gracefully.
            Retry delays back off after repeated transport failures and reset
            after a successful listener session.

            Returns:
                None: This coroutine runs until cancelled.
            """
            retry_delay_seconds = WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS
            while True:
                try:
                    _LOGGER.debug("websocket task listening")
                    await coordinator.api_connection.api_websocket.listener()
                    _LOGGER.debug("websocket task ending")
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                    await asyncio.sleep(retry_delay_seconds)
                    retry_delay_seconds = WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS
                except asyncio.CancelledError:
                    _LOGGER.debug("websocket task cancelled")
                    raise
                except (
                    CarrierUnauthorizedError,
                    ClientError,
                    TimeoutError,
                    OSError,
                    TransportError,
                ):
                    _LOGGER.exception(
                        "websocket task exception; retrying in %s seconds", retry_delay_seconds
                    )
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                    await asyncio.sleep(retry_delay_seconds)
                    retry_delay_seconds = min(
                        retry_delay_seconds * 2, WEBSOCKET_RETRY_MAX_DELAY_SECONDS
                    )

        websocket_task = hass.async_create_background_task(
            ws_updates(),
            f"{DOMAIN}_ws_{config_entry.entry_id}",
        )
        coordinator.websocket_task = websocket_task

        def cancel_websocket_task() -> None:
            """Request websocket listener shutdown during entry unload.

            ConfigEntry.async_on_unload expects a synchronous callback.
            Only cancel the task here; the coordinator keeps the task reference
            and async_unload_entry awaits it so websocket cleanup actually
            finishes before teardown completes.
            """
            websocket_task.cancel()

        config_entry.async_on_unload(cancel_websocket_task)
    except TransportServerError as error:
        _LOGGER.exception("Carrier transport error during setup")
        raise ConfigEntryNotReady(error) from error
    except CarrierUnauthorizedError:
        _LOGGER.exception("Carrier unauthorized during setup")
        raise
    except ConfigEntryNotReady as error:
        if isinstance(error.__cause__, TransportServerError):
            transport_error = error.__cause__
            _LOGGER.exception("Carrier transport error during setup")
            raise ConfigEntryNotReady(transport_error) from transport_error
        if isinstance(error.__cause__, CarrierUnauthorizedError):
            unauthorized_error = error.__cause__
            _LOGGER.exception("Carrier unauthorized during setup")
            raise unauthorized_error from error
        raise

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    config_entry.async_on_unload(config_entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> None:
    """Reload the integration when options are changed.

    Args:
        hass: Home Assistant instance.
        config_entry: Updated configuration entry.

    Returns:
        None: This coroutine schedules and awaits the entry reload.
    """
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> bool:
    """Unload one Carrier config entry and all forwarded platforms.

    Args:
        hass: Home Assistant instance.
        config_entry: Configuration entry being unloaded.

    Returns:
        bool: True when all platforms were unloaded cleanly.
    """
    _LOGGER.debug("unload entry")
    websocket_task = config_entry.runtime_data.websocket_task

    if websocket_task is not None:
        websocket_task.cancel()
        await _async_await_websocket_task(websocket_task)
        config_entry.runtime_data.websocket_task = None

    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
