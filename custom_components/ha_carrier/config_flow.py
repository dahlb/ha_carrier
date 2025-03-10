"""Add and configure integration from UI."""

from logging import Logger, getLogger
from typing import Any

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
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
    CONF_INFINITE_HOLDS,
    DEFAULT_INFINITE_HOLDS,
)

from carrier_api import ApiConnectionGraphql

_LOGGER: Logger = getLogger(__package__)


class OptionFlowHandler(config_entries.OptionsFlow):
    """Display preferences UI."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Display preferences UI."""
        self.schema = vol.Schema(
            {
                vol.Required(
                    CONF_INFINITE_HOLDS,
                    default=config_entry.options.get(
                        CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS
                    ),
                ): cv.boolean,
            }
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Display preferences UI."""
        if user_input is not None:
            _LOGGER.debug("user input in option flow : %s", user_input)
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=self.schema)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlowHandler(config_entries.ConfigFlow):
    """Create instance of integration through UI."""

    VERSION = CONFIG_FLOW_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    data: dict[str, Any] | None = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return preferences handler."""
        return OptionFlowHandler(config_entry)

    def __init__(self):
        """Create instance of integration through UI."""
        pass

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Display auth interface."""
        data_schema = {
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                api_connection = ApiConnectionGraphql(username=username, password=password)
                await api_connection.load_data()
                self.data.update(user_input)
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=username,
                    data=self.data,
                )
            except ConfigEntryAuthFailed:
                errors["base"] = "auth"

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=errors
        )
