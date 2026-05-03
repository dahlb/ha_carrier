"""UI-driven setup, reauth, and options flow for the Carrier integration."""

import logging
from typing import Any

from carrier_api import ApiConnectionGraphql
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_INFINITE_HOLDS,
    CONFIG_FLOW_VERSION,
    DEFAULT_INFINITE_HOLDS,
    DOMAIN,
    ERROR_AUTH,
    ERROR_CANNOT_CONNECT,
    ERROR_UNKNOWN,
)
from .util import RECOVERABLE_REFRESH_EXCEPTIONS, is_unauthorized_error

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def _async_validate_credentials(username: str, password: str) -> dict[str, str]:
    """Validate Carrier credentials with a single API request.

    Args:
        username: Carrier account username.
        password: Carrier account password.

    Returns:
        dict[str, str]: Flow errors keyed by field name, or an empty dict on success.
    """
    api_connection: ApiConnectionGraphql | None = None
    try:
        api_connection = ApiConnectionGraphql(username=username, password=password)
        await api_connection.load_data()
    except ConfigEntryAuthFailed:
        return {"base": ERROR_AUTH}
    except Exception as error:
        if is_unauthorized_error(error):
            return {"base": ERROR_AUTH}
        if isinstance(error, RECOVERABLE_REFRESH_EXCEPTIONS):
            _LOGGER.debug(
                "Transient transport error validating Carrier credentials", exc_info=error
            )
            return {"base": ERROR_CANNOT_CONNECT}
        _LOGGER.exception("Unexpected error validating Carrier credentials")
        return {"base": ERROR_UNKNOWN}
    finally:
        if api_connection is not None:
            await api_connection.cleanup()

    return {}


class OptionFlowHandler(config_entries.OptionsFlow):
    """Handle options updates for an existing Carrier config entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Build the options schema presented to the user.

        Args:
            config_entry: Existing config entry whose options are being edited.
        """
        self.schema = vol.Schema(
            {
                vol.Required(
                    CONF_INFINITE_HOLDS,
                    default=config_entry.options.get(CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS),
                ): cv.boolean,
            }
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Render and process the initial options step.

        Args:
            user_input: Submitted option values when the form is posted.

        Returns:
            ConfigFlowResult: Form response or created options entry.
        """
        if user_input is not None:
            _LOGGER.debug("user input in option flow : %s", user_input)
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=self.schema)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlowHandler(config_entries.ConfigFlow):
    """Authenticate a Carrier account and create or update a config entry."""

    VERSION = CONFIG_FLOW_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    data: dict[str, Any]
    _reauth_username: str

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionFlowHandler:
        """Return the options flow handler for this integration entry.

        Args:
            config_entry: Config entry requesting options management.

        Returns:
            OptionFlowHandler: Options flow implementation for this integration.
        """
        return OptionFlowHandler(config_entry)

    def __init__(self) -> None:
        """Initialize mutable state used while the flow runs."""
        self.data = {}
        self._reauth_username = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle username/password input and validate Carrier credentials.

        Args:
            user_input: Submitted credentials for the Carrier account.

        Returns:
            ConfigFlowResult: Form response with errors or a created config entry.
        """
        errors: dict[str, str] = {}
        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            errors = await _async_validate_credentials(username, password)

            if not errors:
                self.data.update(user_input)
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=username, data=self.data)

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for an existing Carrier config entry.

        Args:
            entry_data: Existing config entry data that triggered reauth.

        Returns:
            ConfigFlowResult: Response for the reauth confirmation step.
        """
        self._reauth_username = entry_data.get(CONF_USERNAME, "")
        if self._reauth_username == "":
            self._reauth_username = self._get_reauth_entry().data.get(CONF_USERNAME, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate new credentials and update the existing config entry.

        Args:
            user_input: Submitted password for the existing Carrier username.

        Returns:
            ConfigFlowResult: Form response with errors or a completed reauth result.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            errors = await _async_validate_credentials(self._reauth_username, password)

            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": self._reauth_username},
            errors=errors,
        )
