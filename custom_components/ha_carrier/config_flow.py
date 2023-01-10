import logging
from typing import Dict, Optional, Any

import voluptuous as vol
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant import config_entries
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    DOMAIN,
    CONFIG_FLOW_VERSION,
)

from carrier_api import ApiConnection

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlowHandler(config_entries.ConfigFlow):

    VERSION = CONFIG_FLOW_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    data: Optional[Dict[str, Any]] = {}

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
