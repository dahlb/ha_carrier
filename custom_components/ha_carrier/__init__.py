"""Initialize and manage the Home Assistant Carrier integration lifecycle."""

import asyncio
import contextlib
from datetime import timedelta
import functools
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

    The coordinator notifies its listeners when a refresh succeeds after a
    failure, so recovery wakes this wait at once. It does not notify them when
    a failing refresh escalates to reauthentication, so the wait also rechecks
    every ``WEBSOCKET_AUTH_RECOVERY_POLL_SECONDS``.

    Args:
        coordinator: Coordinator whose refresh results gate reconnection.

    Returns:
        bool: True once a refresh succeeds; False when the coordinator has
            escalated to reauthentication.
    """
    refreshed = asyncio.Event()

    @callback
    def _async_refreshed() -> None:
        """Wake the wait when the coordinator publishes a refresh."""
        refreshed.set()

    remove_listener = coordinator.async_add_listener(_async_refreshed)
    try:
        while not coordinator.last_update_success:
            if isinstance(coordinator.last_exception, ConfigEntryAuthFailed):
                return False
            refreshed.clear()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(WEBSOCKET_AUTH_RECOVERY_POLL_SECONDS):
                    await refreshed.wait()
    finally:
        remove_listener()
    return True


@callback
def _async_websocket_task_done(
    coordinator: CarrierDataUpdateCoordinator, websocket_task: asyncio.Task[None]
) -> None:
    """Forget a finished websocket task and log how it failed, if it did.

    The task is created with ``ConfigEntry.async_create_background_task``, so
    Home Assistant cancels it and waits for it when the entry unloads, and when
    setup fails after it was started. This callback reports an unexpected
    failure as soon as it happens instead of leaving it unretrieved.

    Args:
        coordinator: Coordinator that tracks the websocket task.
        websocket_task: The websocket task that just finished.
    """
    if coordinator.websocket_task is websocket_task:
        coordinator.websocket_task = None
    if websocket_task.cancelled():
        return
    if (error := websocket_task.exception()) is not None:
        _LOGGER.error(
            "Carrier websocket task stopped unexpectedly: %s",
            type(error).__name__,
            exc_info=error,
        )


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
                    await coordinator.async_request_full_refresh()
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
                    await coordinator.async_request_full_refresh()
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
                    await coordinator.async_request_full_refresh()
                    await asyncio.sleep(delay)
                    attempt += 1
                else:
                    attempt = 0
                    await asyncio.sleep(compute_backoff_delay(WEBSOCKET_RETRY_POLICY, attempt))

        websocket_task = config_entry.async_create_background_task(
            hass,
            ws_updates(),
            f"{DOMAIN}_ws_{config_entry.entry_id}",
        )
        coordinator.websocket_task = websocket_task
        websocket_task.add_done_callback(functools.partial(_async_websocket_task_done, coordinator))
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
    # The websocket task is a config entry background task, so Home Assistant
    # cancels it and waits for it once this returns.
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
