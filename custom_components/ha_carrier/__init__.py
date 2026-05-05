"""Initialize and manage the Home Assistant Carrier integration lifecycle."""

import asyncio
import logging

from carrier_api import ApiConnectionGraphql
from gql.transport.exceptions import TransportServerError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import (
    CONFIG_FLOW_VERSION,
    DOMAIN,
    PLATFORMS,
    RETRY_JITTER_FRACTION,
    TO_REDACT,
    WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS,
    WEBSOCKET_RETRY_MAX_DELAY_SECONDS,
)
from .exceptions import CarrierUnauthorizedError
from .migrate import migrate_1_to_2
from .resiliency import RetryPolicy, compute_backoff_delay
from .util import (
    WEBSOCKET_RECOVERABLE_EXCEPTIONS,
    async_redact_data,
    is_transient_transport_error,
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

type ConfigEntryCarrier = ConfigEntry[CarrierDataUpdateCoordinator]

_LOGGER: logging.Logger = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


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
    except Exception:
        _LOGGER.exception("websocket task raised unexpected exception during cancellation")


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
            attempt = 0
            while True:
                try:
                    _LOGGER.debug("websocket task listening")
                    await coordinator.api_connection.api_websocket.listener()
                    _LOGGER.debug("websocket task ending")
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                    attempt = 0
                    await asyncio.sleep(compute_backoff_delay(WEBSOCKET_RETRY_POLICY, 0))
                except asyncio.CancelledError:
                    _LOGGER.debug("websocket task cancelled")
                    raise
                except WEBSOCKET_RECOVERABLE_EXCEPTIONS as error:
                    if is_unauthorized_error(error):
                        coordinator.resiliency.record_unauthorized(_LOGGER, "websocket listener")
                        _LOGGER.info(
                            "websocket listener received unauthorized error; "
                            "triggering coordinator refresh and retrying"
                        )
                    elif is_transient_transport_error(error):
                        coordinator.resiliency.record_transient(
                            _LOGGER,
                            "websocket listener",
                            error,
                        )
                    else:
                        _LOGGER.exception("websocket task exception")

                    delay = compute_backoff_delay(WEBSOCKET_RETRY_POLICY, attempt)
                    _LOGGER.debug(
                        "websocket task retrying in %.1f seconds (attempt %d)",
                        delay,
                        attempt + 1,
                    )
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()
                    await asyncio.sleep(delay)
                    attempt += 1

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
    except ConfigEntryAuthFailed:
        _LOGGER.exception("Carrier authentication failed during setup")
        raise
    except CarrierUnauthorizedError as error:
        _LOGGER.exception("Carrier unauthorized during setup")
        raise ConfigEntryAuthFailed("Carrier API rejected credentials during setup.") from error
    except TransportServerError as error:
        _LOGGER.exception("Carrier transport error during setup")
        raise ConfigEntryNotReady(error) from error
    except (
        asyncio.CancelledError,
        KeyboardInterrupt,
        SystemExit,
    ):
        raise
    except ConfigEntryNotReady as err:
        if is_unauthorized_error(err):
            _LOGGER.exception("Carrier unauthorized during setup (ConfigEntryNotReady)")
            raise ConfigEntryAuthFailed("Carrier API rejected credentials during setup.") from err
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

    if config_entry.version != 1:
        _LOGGER.error(
            "Unable to migrate Carrier config entry from version %s", config_entry.version
        )
        return False

    if config_entry.version == 1:
        return await migrate_1_to_2(hass=hass, config_entry=config_entry)

    if config_entry.version == 2:
        return True
    return False


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
