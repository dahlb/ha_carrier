"""Initialize and manage the Home Assistant Carrier integration lifecycle."""

import asyncio
from datetime import timedelta
import logging

from carrier_api import CarrierApiConnectionError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_FULL_REFRESH_INTERVAL,
    CONFIG_FLOW_VERSION,
    DEFAULT_FULL_REFRESH_INTERVAL_MINUTES,
    DOMAIN,
    MAX_FULL_REFRESH_INTERVAL_MINUTES,
    MIN_FULL_REFRESH_INTERVAL_MINUTES,
    PLATFORMS,
    RETRY_JITTER_FRACTION,
    TO_REDACT,
    WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS,
    WEBSOCKET_RETRY_MAX_DELAY_SECONDS,
)
from .coordinator import CarrierDataUpdateCoordinator
from .exceptions import CarrierUnauthorizedError
from .migrate import migrate_1_to_2, migrate_2_to_3
from .resiliency import RetryPolicy, compute_backoff_delay
from .services import async_setup_services
from .util import (
    WEBSOCKET_DATA_UPDATE_EXCEPTIONS,
    WEBSOCKET_RECOVERABLE_EXCEPTIONS,
    async_create_api_connection,
    async_redact_data,
    is_unauthorized_error,
)

WEBSOCKET_RETRY_POLICY = RetryPolicy(
    name="carrier-websocket",
    max_attempts=None,
    base_delay=float(WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS),
    max_delay=float(WEBSOCKET_RETRY_MAX_DELAY_SECONDS),
    jitter_fraction=RETRY_JITTER_FRACTION,
    retry_on_unauthorized=False,
)
# The websocket loop calls `compute_backoff_delay` directly, so
# `retry_on_unauthorized` is not enforced by `async_call_with_retry` here. Use
# `async_call_with_retry` with this `WEBSOCKET_RETRY_POLICY` and
# `max_attempts=None` if websocket reconnections should enforce that flag.

# How often the websocket loop rechecks coordinator health after an auth failure.
WEBSOCKET_AUTH_RECOVERY_POLL_SECONDS: float = float(WEBSOCKET_RETRY_MAX_DELAY_SECONDS)

type ConfigEntryCarrier = ConfigEntry[CarrierDataUpdateCoordinator]

_LOGGER: logging.Logger = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide resources that do not depend on a config entry.

    Args:
        hass: Home Assistant instance.
        config: Home Assistant configuration (unused; the integration is
            configured through config entries only).

    Returns:
        bool: True once the integration's service actions are registered.
    """
    async_setup_services(hass)
    return True


async def _async_wait_for_coordinator_recovery(
    coordinator: CarrierDataUpdateCoordinator,
) -> bool:
    """Wait until the coordinator refreshes successfully after an auth failure.

    The Carrier client logs in with the stored password whenever its refresh
    token was rejected, so reconnecting the websocket while credentials are
    failing would retry that password every backoff interval. Waiting here
    leaves login attempts and reauth escalation to the coordinator's own
    polling.

    Args:
        coordinator: Coordinator whose refresh results gate reconnection.

    Returns:
        bool: True once a refresh succeeds; False when the coordinator has
            escalated to reauthentication.
    """
    while not coordinator.last_update_success:
        if isinstance(coordinator.last_exception, ConfigEntryAuthFailed):
            return False
        await asyncio.sleep(WEBSOCKET_AUTH_RECOVERY_POLL_SECONDS)
    return True


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
    except WEBSOCKET_RECOVERABLE_EXCEPTIONS:
        _LOGGER.exception("websocket task raised during cancellation")
    except WEBSOCKET_DATA_UPDATE_EXCEPTIONS:
        _LOGGER.exception("websocket task data update failed before cancellation")
    except RuntimeError:
        _LOGGER.exception("websocket task raised RuntimeError during cancellation")


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
        ConfigEntryAuthFailed: Raised when Carrier setup fails due to invalid
            or expired credentials.
        ConfigEntryNotReady: Raised when initial setup fails for a retryable
            transport or runtime reason.
    """
    _LOGGER.debug(
        "async setup entry: %s",
        async_redact_data(config_entry.as_dict(), TO_REDACT),
    )
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]

    try:
        api_connection = async_create_api_connection(hass, username, password)
        coordinator = CarrierDataUpdateCoordinator(
            hass=hass,
            api_connection=api_connection,
            full_refresh_interval=timedelta(
                minutes=min(
                    max(
                        config_entry.options.get(
                            CONF_FULL_REFRESH_INTERVAL, DEFAULT_FULL_REFRESH_INTERVAL_MINUTES
                        ),
                        MIN_FULL_REFRESH_INTERVAL_MINUTES,
                    ),
                    MAX_FULL_REFRESH_INTERVAL_MINUTES,
                )
            ),
        )
        await coordinator.async_config_entry_first_refresh()
        config_entry.runtime_data = coordinator

        async def ws_updates() -> None:
            """Keep websocket updates running for this config entry.

            The loop exits on cancellation and forces a coordinator refresh if
            websocket handling fails so entity state can recover gracefully.
            Retry delays back off after repeated websocket failures and reset
            after a successful listener session. The websocket loop does not
            update resiliency counters directly; it requests a coordinator
            refresh, and the refresh path owns outage accounting and any reauth
            escalation. After an unauthorized failure the loop waits for a
            successful coordinator refresh before reconnecting, and stops once
            the coordinator escalates to reauthentication; the reauth flow
            reloads the entry, which starts a new websocket task.

            Returns:
                None: This coroutine runs until cancelled.
            """
            attempt = 0
            while True:
                try:
                    _LOGGER.debug("websocket task listening")
                    api_websocket = coordinator.api_connection.api_websocket
                    if api_websocket is None:
                        raise RuntimeError("Carrier API websocket client is not initialized")
                    await api_websocket.listener()
                    _LOGGER.debug("websocket task ending")
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                except asyncio.CancelledError:
                    _LOGGER.debug("websocket task cancelled")
                    raise
                except WEBSOCKET_RECOVERABLE_EXCEPTIONS as error:
                    delay = compute_backoff_delay(WEBSOCKET_RETRY_POLICY, attempt)
                    _LOGGER.debug(
                        "websocket task hit %s; requesting refresh and retrying in %.1f "
                        "seconds (attempt %d)",
                        type(error).__name__,
                        delay,
                        attempt + 1,
                    )
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                    await asyncio.sleep(delay)
                    attempt += 1
                    if is_unauthorized_error(
                        error
                    ) and not await _async_wait_for_coordinator_recovery(coordinator):
                        _LOGGER.warning(
                            "Carrier rejected the stored credentials; websocket updates "
                            "stop until the integration is reauthenticated"
                        )
                        return
                except WEBSOCKET_DATA_UPDATE_EXCEPTIONS as error:
                    delay = compute_backoff_delay(WEBSOCKET_RETRY_POLICY, attempt)
                    # Log the stack trace once per failure streak; repeats until the
                    # listener next succeeds only go to debug.
                    _LOGGER.log(
                        logging.ERROR if attempt == 0 else logging.DEBUG,
                        "websocket data update failed with %s; requesting refresh and "
                        "retrying in %.1f seconds (attempt %d)",
                        type(error).__name__,
                        delay,
                        attempt + 1,
                        exc_info=error,
                    )
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                    await asyncio.sleep(delay)
                    attempt += 1
                else:
                    attempt = 0
                    await asyncio.sleep(compute_backoff_delay(WEBSOCKET_RETRY_POLICY, attempt))

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
    # Home Assistant logs a not-ready setup (and each background retry) and
    # starts reauthentication itself, so these paths only debug-log the cause to
    # avoid repeating a stack trace on every setup retry while Carrier is down.
    except ConfigEntryAuthFailed:
        _LOGGER.debug("Carrier authentication failed during setup", exc_info=True)
        raise
    except CarrierUnauthorizedError as error:
        _LOGGER.debug("Carrier unauthorized during setup", exc_info=error)
        raise ConfigEntryAuthFailed("Carrier API rejected credentials during setup.") from error
    except CarrierApiConnectionError as error:
        _LOGGER.debug("Carrier connection error during setup", exc_info=error)
        raise ConfigEntryNotReady(error) from error
    except (
        asyncio.CancelledError,
        KeyboardInterrupt,
        SystemExit,
    ):
        raise
    except ConfigEntryNotReady as err:
        if is_unauthorized_error(err):
            _LOGGER.debug(
                "Carrier authentication failed during initial setup refresh", exc_info=err
            )
            raise ConfigEntryAuthFailed("Carrier API rejected credentials during setup.") from err
        _LOGGER.debug("Carrier setup is not ready; Home Assistant will retry", exc_info=err)
        raise

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    @callback
    def _async_remove_stale_devices_listener() -> None:
        """Drop devices for systems that left the account after each refresh."""
        _async_remove_stale_devices(hass, config_entry)

    _async_remove_stale_devices(hass, config_entry)
    config_entry.async_on_unload(
        coordinator.async_add_listener(_async_remove_stale_devices_listener)
    )
    config_entry.async_on_unload(config_entry.add_update_listener(async_update_options))

    return True


def _current_serials(coordinator: CarrierDataUpdateCoordinator) -> set[str]:
    """Return the serials of every system the Carrier account currently reports.

    Args:
        coordinator: Coordinator tracking the account's systems.

    Returns:
        set[str]: Infinity / Evolution and entry-level system serials.
    """
    return {system.profile.serial for system in coordinator.systems} | {
        system.serial for system in coordinator.entry_level_systems
    }


def _device_serials(device: dr.DeviceEntry) -> set[str]:
    """Return the Carrier serials a device registry entry is identified by.

    Args:
        device: Device registry entry.

    Returns:
        set[str]: Serials from the device's ``ha_carrier`` identifiers.
    """
    return {identifier for domain, identifier in device.identifiers if domain == DOMAIN}


@callback
def _async_remove_stale_devices(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> None:
    """Remove devices whose Carrier system is no longer on the account.

    The coordinator drops a system from ``systems`` after a full refresh that no
    longer returns it. Its device, and with it its entities, are removed here.
    Cleanup is skipped unless the latest entry-level load succeeded, because a
    failed load leaves that list incomplete, and skipped entirely when the
    account reports no systems at all, which is far likelier to be a Carrier
    glitch than every thermostat being removed at once.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier config entry whose devices are checked.
    """
    coordinator = config_entry.runtime_data
    if not coordinator.entry_level_systems_loaded:
        return
    current_serials = _current_serials(coordinator)
    if not current_serials:
        return
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, config_entry.entry_id):
        serials = _device_serials(device)
        if serials and not serials & current_serials:
            _LOGGER.info(
                "Removing Carrier device %s; its system is no longer on the account",
                device.name,
            )
            device_registry.async_update_device(
                device.id, remove_config_entry_id=config_entry.entry_id
            )


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntryCarrier, device_entry: dr.DeviceEntry
) -> bool:
    """Let the user delete a device only once Carrier no longer reports it.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier config entry the device belongs to.
        device_entry: Device the user asked to delete.

    Returns:
        bool: True when none of the device's serials is a current system.
    """
    return not _device_serials(device_entry) & _current_serials(config_entry.runtime_data)


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> None:
    """Reload the integration when options are changed.

    Args:
        hass: Home Assistant instance.
        config_entry: Updated configuration entry.

    Returns:
        None: This coroutine schedules and awaits the entry reload.
    """
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntryCarrier) -> bool:
    """Migrate Carrier config entries between config flow versions.

    Args:
        hass: Home Assistant instance.
        config_entry: Configuration entry being migrated.

    Returns:
        bool: True when migration succeeds.
    """
    _LOGGER.debug("Migrating Carrier config entry from version %s", config_entry.version)

    if config_entry.version == CONFIG_FLOW_VERSION:
        return True

    if config_entry.version not in {1, 2}:
        _LOGGER.error(
            "Unable to migrate Carrier config entry from version %s", config_entry.version
        )
        return False

    if config_entry.version == 1:
        if not await migrate_1_to_2(hass=hass, config_entry=config_entry):
            return False
        if config_entry.version != 2:
            return True

    if config_entry.version == 2:
        return await migrate_2_to_3(hass=hass, config_entry=config_entry)

    return config_entry.version == CONFIG_FLOW_VERSION


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
