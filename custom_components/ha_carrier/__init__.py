"""Setup integration ha_carrier."""

import voluptuous as vol
from logging import Logger, getLogger
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv
from homeassistant.exceptions import ConfigEntryNotReady

from carrier_api import ApiConnection

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import (
    DOMAIN,
    PLATFORMS,
    DATA_SYSTEMS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    TO_REDACT,
)
from .util import async_redact_data

LOGGER: Logger = getLogger(__package__)

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


async def async_setup(hass: HomeAssistant, config_entry: ConfigType) -> bool:
    """Create global variables for integration."""
    hass.data.setdefault(DOMAIN, {})
    LOGGER.debug("async setup")
    getLogger('carrier_api').setLevel(LOGGER.level)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Create instance of integration."""
    LOGGER.debug(f"async setup entry: {async_redact_data(config_entry.as_dict(), TO_REDACT)}")
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    data = {}

    try:
        api_connection = ApiConnection(username=username, password=password)
        carrier_systems = await hass.async_add_executor_job(api_connection.get_systems)
    except Exception as error:
        raise ConfigEntryNotReady(error) from error

    def create_updaters(carrier_system):
        return CarrierDataUpdateCoordinator(
            hass=hass,
            carrier_system=carrier_system,
            interval=interval,
        )

    data[DATA_SYSTEMS] = list(map(create_updaters, carrier_systems))

    for updater in data[DATA_SYSTEMS]:
        await updater.async_config_entry_first_refresh()

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
    LOGGER.debug("unload entry")
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if unload_ok:
        hass.data[DOMAIN][config_entry.entry_id] = None

    return unload_ok
