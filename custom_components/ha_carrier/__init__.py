"""Setup integration ha_carrier."""
import asyncio

import voluptuous as vol
from logging import Logger, getLogger
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.config_entries import ConfigEntry
import homeassistant.helpers.config_validation as cv
from homeassistant.exceptions import ConfigEntryNotReady

from carrier_api import ApiConnectionGraphql

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import (
    DOMAIN,
    PLATFORMS,
    TO_REDACT,
    DATA_UPDATE_COORDINATOR,
)
from .util import async_redact_data

_LOGGER: Logger = getLogger(__package__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Create global variables for integration."""
    hass.data.setdefault(DOMAIN, {})
    _LOGGER.debug("async setup")
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Create instance of integration."""
    _LOGGER.debug(f"async setup entry: {async_redact_data(config_entry.as_dict(), TO_REDACT)}")
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]

    data = {}

    try:
        api_connection = ApiConnectionGraphql(username=username, password=password)
        data[DATA_UPDATE_COORDINATOR] = CarrierDataUpdateCoordinator(
            hass=hass,
            api_connection=api_connection,
        )
        await data[DATA_UPDATE_COORDINATOR].async_config_entry_first_refresh()
        async def ws_updates():
            running = True
            while running:
                try:
                    _LOGGER.debug("websocket task listening")
                    await data[DATA_UPDATE_COORDINATOR].api_connection.api_websocket.listener()
                    _LOGGER.debug("websocket task ending")
                except asyncio.CancelledError:
                    running = False
                    _LOGGER.debug("websocket task cancelled")
                except Exception as websocket_error:
                    _LOGGER.exception("websocket task exception", exc_info=websocket_error)
                    await data[DATA_UPDATE_COORDINATOR].async_request_refresh()
        hass.async_create_background_task(ws_updates(), "ha_carrier_ws")
    except Exception as error:
        _LOGGER.exception(error)
        raise ConfigEntryNotReady(error) from error

    hass.data[DOMAIN][config_entry.entry_id] = data

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    return True


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Update preferences for integration instance."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Cleanup instance of integration."""
    _LOGGER.debug("unload entry")
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if unload_ok:
        hass.data[DOMAIN][config_entry.entry_id] = None

    return unload_ok
