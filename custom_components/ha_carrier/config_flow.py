import logging
from typing import Dict, Optional, Any

import voluptuous as vol
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    DOMAIN,
    CONFIG_FLOW_VERSION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)

from carrier_api import ApiConnection

_LOGGER = logging.getLogger(__name__)


class OptionFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self.schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=20)),
            }
        )

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            _LOGGER.debug(f"user input in option flow : %s", user_input)
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=self.schema)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlowHandler(config_entries.ConfigFlow):

    VERSION = CONFIG_FLOW_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    data: Optional[Dict[str, Any]] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionFlowHandler(config_entry)

    def __init__(self):
        pass

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        data_schema = {
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
        errors: Dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                api_connection = ApiConnection(username=username, password=password)
                await self.hass.async_add_executor_job(api_connection.get_systems)
                self.data.update(user_input)
                return self.async_create_entry(
                    title=username,
                    data=self.data,
                )
            except ConfigEntryAuthFailed:
                errors["base"] = "auth"

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=errors
        )
