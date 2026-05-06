"""UI-driven setup, reauth, and options flow for the Carrier integration."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError
from carrier_api import ApiConnectionGraphql, AuthError, BaseError
from gql.transport.exceptions import TransportError
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
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
from .util import async_get_carrier_identity_id, is_transient_transport_error, is_unauthorized_error

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def _async_validate_credentials(
    username: str,
    password: str,
) -> tuple[dict[str, str], str | None]:
    """Validate Carrier credentials with a single API request.

    Args:
        username: Carrier account username.
        password: Carrier account password.

    Returns:
        tuple[dict[str, str], str | None]: Flow errors keyed by field name and
            the Carrier identity ID when validation succeeds.
    """
    api_connection: ApiConnectionGraphql | None = None
    try:
        api_connection = ApiConnectionGraphql(username=username, password=password)
        identity_id = await async_get_carrier_identity_id(api_connection)
    except (AuthError, BaseError, ClientError, TransportError, OSError, TimeoutError) as error:
        if is_unauthorized_error(error):
            return {"base": ERROR_AUTH}, None
        if is_transient_transport_error(error):
            _LOGGER.debug(
                "Transient transport error validating Carrier credentials", exc_info=error
            )
            return {"base": ERROR_CANNOT_CONNECT}, None
        _LOGGER.exception("Unexpected error validating Carrier credentials")
        return {"base": ERROR_UNKNOWN}, None
    finally:
        if api_connection is not None:
            try:
                await api_connection.cleanup()
            except AuthError, BaseError, ClientError, TransportError, OSError, TimeoutError:
                _LOGGER.exception(
                    "Failed to clean up Carrier API connection after credential validation"
                )
    if identity_id is None:
        _LOGGER.error("Carrier API did not return an identity ID for validated credentials")
        return {"base": ERROR_UNKNOWN}, None

    return {}, identity_id


class CarrierConfigFlow(ConfigFlow, domain=DOMAIN):
    """Authenticate a Carrier account and create or update a config entry."""

    VERSION = CONFIG_FLOW_VERSION

    data: dict[str, Any]
    _reauth_entry_data: dict[str, Any]

    def __init__(self) -> None:
        """Initialize mutable state used while the flow runs."""
        self.data = {}
        self._reauth_entry_data = {}

    @staticmethod
    def _allows_legacy_reauth_unique_id_transition(
        reauth_entry: ConfigEntry,
        identity_id: str,
    ) -> bool:
        """Return whether reauth may replace a legacy username unique ID.

        Args:
            reauth_entry: Existing config entry being reauthenticated.
            identity_id: Carrier identity ID returned for the new credentials.

        Returns:
            bool: True when the entry still uses its saved username as the
                unique ID and should be allowed to migrate to ``identityId``
                during reauth.
        """
        entry_unique_id = reauth_entry.unique_id
        entry_username = reauth_entry.data.get(CONF_USERNAME)
        return (
            isinstance(entry_unique_id, str)
            and isinstance(entry_username, str)
            and entry_unique_id == entry_username
            and entry_unique_id != identity_id
        )

    @staticmethod
    def _credentials_schema(
        username: str | None = None,
        *,
        require_password: bool,
    ) -> vol.Schema:
        """Build a Carrier credential form schema.

        Args:
            username: Existing Carrier account username to show as the default.
            require_password: Whether the password field must be submitted.

        Returns:
            vol.Schema: Credential form schema for the requested flow.
        """
        schema: dict[vol.Marker, type[str]] = {}
        if username is None:
            schema[vol.Required(CONF_USERNAME)] = str
        else:
            schema[vol.Required(CONF_USERNAME, default=username)] = str

        password_marker: vol.Marker
        if require_password:
            password_marker = vol.Required(CONF_PASSWORD)
        else:
            password_marker = vol.Optional(CONF_PASSWORD)
        schema[password_marker] = str

        return vol.Schema(schema)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle username/password input and validate Carrier credentials.

        Args:
            user_input: Submitted credentials for the Carrier account.

        Returns:
            ConfigFlowResult: Form response with errors or a created config entry.
        """
        errors: dict[str, str] = {}
        data_schema = self._credentials_schema(require_password=True)

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            errors, identity_id = await _async_validate_credentials(username, password)

            if not errors:
                if identity_id is None:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=data_schema,
                        errors={"base": ERROR_UNKNOWN},
                    )
                await self.async_set_unique_id(identity_id)
                self._abort_if_unique_id_configured()
                self.data.update(user_input)
                return self.async_create_entry(title=username, data=self.data)

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate credentials and update an existing Carrier config entry.

        Args:
            user_input: Submitted credentials for the existing Carrier config entry.

        Returns:
            ConfigFlowResult: Form response with errors or a completed reconfigure result.
        """
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        entry_username = reconfigure_entry.data.get(CONF_USERNAME)
        entry_username = entry_username if isinstance(entry_username, str) else ""

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input.get(CONF_PASSWORD) or reconfigure_entry.data[CONF_PASSWORD]

            errors, identity_id = await _async_validate_credentials(username, password)

            if not errors:
                if identity_id is None:
                    errors = {"base": ERROR_UNKNOWN}
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=self._credentials_schema(
                            entry_username,
                            require_password=False,
                        ),
                        description_placeholders={"username": entry_username},
                        errors=errors,
                    )
                existing_entry = await self.async_set_unique_id(identity_id)
                if not self._allows_legacy_reauth_unique_id_transition(
                    reconfigure_entry,
                    identity_id,
                ):
                    self._abort_if_unique_id_mismatch()
                if (
                    existing_entry is not None
                    and existing_entry.entry_id != reconfigure_entry.entry_id
                ):
                    self._abort_if_unique_id_configured()
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    unique_id=identity_id,
                    title=username,
                    data_updates={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._credentials_schema(
                entry_username,
                require_password=False,
            ),
            description_placeholders={"username": entry_username},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for an existing Carrier config entry.

        Args:
            entry_data: Existing config entry data that triggered reauth.

        Returns:
            ConfigFlowResult: Response for the reauth confirmation step.
        """
        self._reauth_entry_data = dict(self._get_reauth_entry().data)
        self._reauth_entry_data.update(entry_data)

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate new credentials and update the existing config entry.

        Args:
            user_input: Submitted credentials for the existing Carrier config entry.

        Returns:
            ConfigFlowResult: Form response with errors or a completed reauth result.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input.get(CONF_PASSWORD) or self._reauth_entry_data[CONF_PASSWORD]
            reauth_entry = self._get_reauth_entry()

            errors, identity_id = await _async_validate_credentials(username, password)

            if not errors:
                if identity_id is None:
                    errors = {"base": ERROR_UNKNOWN}
                    return self.async_show_form(
                        step_id="reauth_confirm",
                        data_schema=self._credentials_schema(
                            self._reauth_entry_data.get(CONF_USERNAME, ""),
                            require_password=False,
                        ),
                        description_placeholders={
                            "username": self._reauth_entry_data.get(CONF_USERNAME, "")
                        },
                        errors=errors,
                    )
                existing_entry = await self.async_set_unique_id(identity_id)
                if not self._allows_legacy_reauth_unique_id_transition(
                    reauth_entry,
                    identity_id,
                ):
                    self._abort_if_unique_id_mismatch()
                if existing_entry is not None and existing_entry.entry_id != reauth_entry.entry_id:
                    self._abort_if_unique_id_configured()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    unique_id=identity_id,
                    title=username,
                    data_updates={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self._credentials_schema(
                self._reauth_entry_data.get(CONF_USERNAME, ""),
                require_password=False,
            ),
            description_placeholders={"username": self._reauth_entry_data.get(CONF_USERNAME, "")},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CarrierOptionsFlow:
        """Return the options flow handler for this integration entry.

        Args:
            config_entry: Config entry requesting options management.

        Returns:
            CarrierOptionsFlow: Options flow implementation for this integration.
        """
        return CarrierOptionsFlow(config_entry)


class CarrierOptionsFlow(OptionsFlow):
    """Handle options updates for an existing Carrier config entry."""

    def __init__(self, config_entry: ConfigEntry) -> None:
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
