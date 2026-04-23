"""Update data from carrier api."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger
from typing import Any

from carrier_api import ApiConnectionGraphql, Energy, System
from carrier_api.api_websocket_data_updater import WebsocketDataUpdater
from gql.transport.exceptions import TransportServerError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, TO_REDACT_MAPPED
from .util import async_redact_data

_LOGGER: Logger = getLogger(__package__)
DEFAULT_UPDATE_INTERVAL_MINUTES = 30
UNAUTHORIZED_RETRY_THRESHOLD = 3
WRITE_RETRY_DELAY_SECONDS = 1


class CarrierUnauthorizedError(Exception):
    """Raised when unauthorized responses stop looking transient."""


class CarrierDataUpdateCoordinator(DataUpdateCoordinator):
    """Update data from carrier api."""

    systems: list[System] = None
    websocket_data_updater: WebsocketDataUpdater = None
    data_flush: bool = True
    timestamp_all_data = None
    timestamp_websocket = None
    timestamp_energy = None

    def __init__(
        self,
        hass: HomeAssistant,
        api_connection: ApiConnectionGraphql,
    ) -> None:
        """Initialize the device."""
        self.hass: HomeAssistant = hass
        self.api_connection: ApiConnectionGraphql = api_connection
        self.consecutive_unauthorized_count = 0
        self.unauthorized_outage_logged = False
        self.unauthorized_escalated_logged = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.api_connection.username}",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
            always_update=False,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REQUEST_REFRESH_DEFAULT_COOLDOWN,
                immediate=False,
                function=self.async_refresh,
            ),
        )

    async def _async_update_data(self):
        refresh_context = "full data refresh" if self.data_flush else "energy refresh"
        try:
            if self.data_flush:
                _LOGGER.debug("fetching fresh all data")
                fresh_systems: list[System] = await self.api_connection.load_data()
                if self.systems is None:
                    self.systems = fresh_systems
                    self.websocket_data_updater = WebsocketDataUpdater(systems=self.systems)
                    self.api_connection.api_websocket.callback_add(
                        self.websocket_data_updater.message_handler
                    )
                    self.api_connection.api_websocket.callback_add(self.updated_callback)
                else:
                    for fresh_system in fresh_systems:
                        related_stale_system = self.system(fresh_system.profile.serial)
                        if related_stale_system is None:
                            _LOGGER.error(
                                f"unable to find matching system, serial {fresh_system.profile.serial}"
                            )
                        else:
                            related_stale_system.profile = fresh_system.profile
                            related_stale_system.status = fresh_system.status
                            related_stale_system.config = fresh_system.config
                            related_stale_system.energy = fresh_system.energy
                for system in self.systems:
                    _LOGGER.debug(async_redact_data(system.__repr__(), TO_REDACT_MAPPED))
                self.timestamp_all_data = datetime.now(UTC)
                self.timestamp_energy = self.timestamp_all_data
                self.data_flush = False
                self._reset_unauthorized_tracking()
                self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)
            else:
                _LOGGER.debug("fetching energy data")
                found_unauthorized = False
                for system in self.systems:
                    try:
                        energy_response = await self.api_connection.get_energy(
                            system.profile.serial
                        )
                    except TransportServerError as server_error:
                        if not self._is_unauthorized_error(server_error):
                            raise
                        found_unauthorized = True
                        continue
                    energy = Energy(raw=energy_response["infinityEnergy"])
                    system.energy = energy
                if found_unauthorized:
                    should_escalate = self._record_unauthorized("energy refresh cycle")
                    if should_escalate:
                        raise CarrierUnauthorizedError(
                            "Carrier API repeatedly rejected energy refresh requests; check credentials or service health."
                        )
                if not found_unauthorized:
                    self.timestamp_energy = datetime.now(UTC)
                    self._reset_unauthorized_tracking()
                    self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)
                else:
                    self.update_interval = timedelta(minutes=1)
            return [system.__repr__() for system in self.systems]
        except CarrierUnauthorizedError as error:
            self.data_flush = True
            self.update_interval = timedelta(minutes=1)
            raise UpdateFailed(str(error)) from error
        except TransportServerError as server_error:
            self.data_flush = True
            if self._is_unauthorized_error(server_error):
                should_escalate = self._record_unauthorized(refresh_context)
                self.update_interval = timedelta(minutes=1)
                if should_escalate:
                    raise UpdateFailed(
                        "Carrier API repeatedly rejected refresh requests; check credentials or service health."
                    ) from server_error
                raise UpdateFailed(
                    "Carrier API temporarily rejected the refresh; retrying soon."
                ) from server_error
            _LOGGER.exception(server_error)
            _LOGGER.debug("transport error likely carrier api maintenance so retrying in 1 minute.")
            self.update_interval = timedelta(minutes=1)
            raise UpdateFailed(server_error) from server_error
        except Exception as error:
            _LOGGER.exception(error)
            self.data_flush = True
            self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)
            _LOGGER.debug(
                "unrecognized error so retrying in default 30 minutes but refreshing all data then."
            )
            raise UpdateFailed(error) from error

    @staticmethod
    def _is_unauthorized_error(error: Exception) -> bool:
        """Return true when carrier rejected the request as unauthorized."""
        status_code = getattr(error, "code", None) or getattr(error, "status", None)
        if status_code == 401:
            return True
        error_message = str(error)
        return "401" in error_message and "Unauthorized" in error_message

    def _reset_unauthorized_tracking(self) -> None:
        """Reset intermittent auth-blip tracking after a successful request."""
        self.consecutive_unauthorized_count = 0
        self.unauthorized_outage_logged = False
        self.unauthorized_escalated_logged = False

    def _record_unauthorized(self, context: str) -> bool:
        """Track unauthorized responses and rate-limit log noise."""
        self.consecutive_unauthorized_count += 1
        if not self.unauthorized_outage_logged:
            _LOGGER.info(
                "Carrier API returned unauthorized during %s; treating it as a transient blip.",
                context,
            )
            self.unauthorized_outage_logged = True
        if (
            self.consecutive_unauthorized_count >= UNAUTHORIZED_RETRY_THRESHOLD
            and not self.unauthorized_escalated_logged
        ):
            _LOGGER.error(
                "Carrier API returned unauthorized %s consecutive times; this no longer looks transient.",
                self.consecutive_unauthorized_count,
            )
            self.unauthorized_escalated_logged = True
        return self.consecutive_unauthorized_count >= UNAUTHORIZED_RETRY_THRESHOLD

    def _is_retryable_write_error(self, error: Exception) -> bool:
        """Return true when a write failure should be retried once."""
        if isinstance(error, TransportServerError):
            return self._is_unauthorized_error(error)
        return isinstance(error, TimeoutError)

    async def _async_retry_write(self, attempt: int) -> bool:
        """Delay and indicate whether another write attempt should be made."""
        if attempt != 0:
            return False
        await asyncio.sleep(WRITE_RETRY_DELAY_SECONDS)
        return True

    async def _async_handle_failed_write(self, operation_name: str, error: Exception) -> None:
        """Refresh after a write failure and raise a user-facing error."""
        is_unauthorized_write = isinstance(
            error, TransportServerError
        ) and self._is_unauthorized_error(error)
        should_escalate = False

        if is_unauthorized_write:
            should_escalate = self._record_unauthorized(operation_name)

        self.data_flush = True
        try:
            await self.async_refresh()
        except Exception as refresh_error:  # pragma: no cover - defensive logging only
            _LOGGER.debug(
                "refresh after failed %s write failed: %s",
                operation_name,
                refresh_error,
            )

        if is_unauthorized_write:
            if should_escalate:
                raise HomeAssistantError(
                    "Carrier repeatedly rejected requests. Check credentials or Carrier service health."
                ) from error
            raise HomeAssistantError(
                "Carrier temporarily rejected the request. Try again shortly."
            ) from error

        raise HomeAssistantError(
            "Carrier timed out while applying the request. Try again shortly."
        ) from error

    async def async_perform_api_call(
        self,
        operation_name: str,
        request: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Perform a write call with a single retry for transient Carrier API failures."""
        for attempt in range(2):
            try:
                result = await request()
            except (TransportServerError, TimeoutError) as error:
                if not self._is_retryable_write_error(error):
                    raise
                if await self._async_retry_write(attempt):
                    continue
                await self._async_handle_failed_write(operation_name, error)
                raise
            else:
                self._reset_unauthorized_tracking()
                return result

    def system(self, system_serial: str) -> System | None:
        for system in self.systems:
            if system.profile.serial == system_serial:
                return system

    async def updated_callback(self, _message: str) -> None:
        self.timestamp_websocket = datetime.now(UTC)
        _LOGGER.debug("websocket updated system")
        for system in self.systems:
            _LOGGER.debug(async_redact_data(system.__repr__(), TO_REDACT_MAPPED))
        self.async_update_listeners()
