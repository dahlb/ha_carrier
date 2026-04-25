"""Initialize and manage the Home Assistant Carrier integration lifecycle."""

import asyncio
from logging import Logger, getLogger

from carrier_api import ApiConnectionGraphql
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import DOMAIN, PLATFORMS, TO_REDACT
from .util import async_redact_data

_LOGGER: Logger = getLogger(__package__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
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

            Returns:
                None: This coroutine runs until cancelled.
            """
            running = True
            while running:
                try:
                    _LOGGER.debug("websocket task listening")
                    await coordinator.api_connection.api_websocket.listener()
                    _LOGGER.debug("websocket task ending")
                except asyncio.CancelledError:
                    running = False
                    _LOGGER.debug("websocket task cancelled")
                except Exception as websocket_error:
                    _LOGGER.exception("websocket task exception", exc_info=websocket_error)
                    coordinator.data_flush = True
                    await coordinator.async_request_refresh()

        websocket_task = hass.async_create_background_task(
            ws_updates(),
            f"{DOMAIN}_ws_{config_entry.entry_id}",
        )

        def cancel_websocket_task() -> None:
            """Cancel the websocket listener task during entry unload."""
            websocket_task.cancel()

        config_entry.async_on_unload(cancel_websocket_task)
    except Exception as error:
        _LOGGER.exception("failed to set up Carrier integration")
        raise ConfigEntryNotReady(error) from error

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    config_entry.async_on_unload(config_entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload the integration when options are changed.

    Args:
        hass: Home Assistant instance.
        config_entry: Updated configuration entry.

    Returns:
        None: This coroutine schedules and awaits the entry reload.
    """
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload one Carrier config entry and all forwarded platforms.

    Args:
        hass: Home Assistant instance.
        config_entry: Configuration entry being unloaded.

    Returns:
        bool: True when all platforms were unloaded cleanly.
    """
    _LOGGER.debug("unload entry")
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
