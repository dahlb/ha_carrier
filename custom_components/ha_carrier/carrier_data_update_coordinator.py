"""Coordinate polling, websocket updates, and writes for Carrier systems."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger
from typing import Any, NoReturn

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
MAX_WRITE_ATTEMPTS = 2


class CarrierUnauthorizedError(Exception):
    """Raised when unauthorized responses stop looking transient."""


class CarrierDataUpdateCoordinator(DataUpdateCoordinator[list[str]]):
    """Maintain synchronized Carrier system data for all integration entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_connection: ApiConnectionGraphql,
    ) -> None:
        """Initialize coordinator state and refresh scheduling.

        Args:
            hass: Home Assistant instance used for task scheduling and callbacks.
            api_connection: Authenticated Carrier API connection wrapper.
        """
        self.hass: HomeAssistant = hass
        self.api_connection: ApiConnectionGraphql = api_connection
        self.consecutive_unauthorized_count = 0
        self.unauthorized_outage_logged = False
        self.unauthorized_escalated_logged = False
        self._suppress_unauthorized_recording = False
        self.systems: list[System] = []
        self.websocket_data_updater: WebsocketDataUpdater | None = None
        self._websocket_initialized = False
        self.data_flush = True
        self.timestamp_all_data: datetime | None = None
        self.timestamp_websocket: datetime | None = None
        self.timestamp_energy: datetime | None = None

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

    async def _async_update_data(self) -> list[str]:
        """Fetch the latest Carrier data for entities backed by the coordinator.

        Performs either a full system refresh or a lighter energy-only refresh,
        depending on whether the coordinator has been marked dirty. Unauthorized
        responses are tracked separately so transient Carrier outages can be
        retried quickly without immediately treating credentials as invalid.

        Returns:
            list[str]: String representations of the tracked systems.

        Raises:
            UpdateFailed: Raised when the refresh cannot complete successfully.
        """
        refresh_context = "full data refresh" if self.data_flush else "energy refresh"
        try:
            if self.data_flush:
                _LOGGER.debug("fetching fresh all data")
                fresh_systems: list[System] = await self.api_connection.load_data()
                if not self.systems:
                    self.systems = fresh_systems
                else:
                    for fresh_system in fresh_systems:
                        related_stale_system = self.system(fresh_system.profile.serial)
                        if related_stale_system is None:
                            _LOGGER.error(
                                "unable to find matching system, serial %s",
                                fresh_system.profile.serial,
                            )
                        else:
                            related_stale_system.profile = fresh_system.profile
                            related_stale_system.status = fresh_system.status
                            related_stale_system.config = fresh_system.config
                            related_stale_system.energy = fresh_system.energy
                if not self._websocket_initialized:
                    self.websocket_data_updater = WebsocketDataUpdater(systems=self.systems)
                    self.api_connection.api_websocket.callback_add(
                        self.websocket_data_updater.message_handler
                    )
                    self.api_connection.api_websocket.callback_add(self.updated_callback)
                    self._websocket_initialized = True
                for system in self.systems:
                    _LOGGER.debug(
                        "%s",
                        async_redact_data(self.mapped_system_data(system), TO_REDACT_MAPPED),
                    )
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
                        # Preserve the last known energy payload while probing whether
                        # the unauthorized response is just a transient Carrier outage.
                        found_unauthorized = True
                        continue
                    energy = Energy(raw=energy_response["infinityEnergy"])
                    system.energy = energy
                if found_unauthorized:
                    if self._record_unauthorized("energy refresh cycle"):
                        raise CarrierUnauthorizedError(
                            "Carrier API repeatedly rejected energy refresh requests; "
                            "check credentials or service health."
                        )
                    self.update_interval = timedelta(minutes=1)
                else:
                    self.timestamp_energy = datetime.now(UTC)
                    self._reset_unauthorized_tracking()
                    self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)
            return [self.mapped_system_data(system) for system in self.systems]
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
                        "Carrier API repeatedly rejected refresh requests; "
                        "check credentials or service health."
                    ) from server_error
                raise UpdateFailed(
                    "Carrier API temporarily rejected the refresh; retrying soon."
                ) from server_error
            _LOGGER.exception("Carrier refresh hit a transport server error")
            _LOGGER.debug("transport error likely carrier api maintenance so retrying in 1 minute.")
            self.update_interval = timedelta(minutes=1)
            raise UpdateFailed(server_error) from server_error
        except Exception as error:  # pragma: no cover - defensive logging only
            _LOGGER.exception("Carrier refresh failed with an unexpected error")
            self.data_flush = True
            self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)
            _LOGGER.debug(
                "unrecognized error so retrying in default 30 minutes but refreshing all data then."
            )
            raise UpdateFailed(error) from error

    @staticmethod
    def _is_unauthorized_error(error: Exception) -> bool:
        """Determine whether an exception represents a Carrier unauthorized response.

        Args:
            error: Exception raised by the Carrier client or transport.

        Returns:
            bool: True when the error maps to an HTTP 401-style failure.
        """
        status_code = getattr(error, "code", None) or getattr(error, "status", None)
        return status_code == 401

    def _reset_unauthorized_tracking(self) -> None:
        """Clear the unauthorized counters after a successful Carrier request.

        A successful read or write means the most recent authentication issue was
        transient, so subsequent failures should start a new outage window.
        """
        self.consecutive_unauthorized_count = 0
        self.unauthorized_outage_logged = False
        self.unauthorized_escalated_logged = False

    def _record_unauthorized(self, context: str) -> bool:
        """Record an unauthorized response and decide whether to escalate it.

        Args:
            context: Short description of the request path that failed.

        Returns:
            bool: True when repeated unauthorized responses have crossed the
            escalation threshold.
        """
        if self._suppress_unauthorized_recording:
            return self.consecutive_unauthorized_count >= UNAUTHORIZED_RETRY_THRESHOLD

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
                "Carrier API returned unauthorized %s consecutive times; "
                "this no longer looks transient.",
                self.consecutive_unauthorized_count,
            )
            self.unauthorized_escalated_logged = True
        return self.consecutive_unauthorized_count >= UNAUTHORIZED_RETRY_THRESHOLD

    def _is_retryable_write_error(self, error: Exception) -> bool:
        """Return whether a write failure should be retried once.

        Args:
            error: Exception raised while sending a Carrier write request.

        Returns:
            bool: True when the failure matches a transient unauthorized or
            timeout condition.
        """
        if isinstance(error, TransportServerError):
            return self._is_unauthorized_error(error)
        return isinstance(error, TimeoutError)

    async def _async_retry_write(self, attempt: int) -> bool:
        """Delay before retrying a failed write when attempts remain.

        Args:
            attempt: Zero-based attempt number for the current request.

        Returns:
            bool: True when the caller should issue one more write attempt.
        """
        if attempt >= MAX_WRITE_ATTEMPTS - 1:
            return False
        await asyncio.sleep(WRITE_RETRY_DELAY_SECONDS)
        return True

    async def _async_handle_failed_write(self, operation_name: str, error: Exception) -> NoReturn:
        """Recover from a failed write and raise a user-facing Home Assistant error.

        Forces a refresh so entity state is reconciled with the Carrier backend
        before surfacing the failure to the user.

        Args:
            operation_name: Friendly name for the write operation that failed.
            error: Exception raised by the Carrier API client.

        Raises:
            HomeAssistantError: Raised with a message tailored to the failure type.
        """
        is_unauthorized_write = isinstance(
            error, TransportServerError
        ) and self._is_unauthorized_error(error)
        should_escalate = False

        if is_unauthorized_write:
            should_escalate = self._record_unauthorized(operation_name)

        await self._async_reconcile_failed_write(operation_name, error)

        if is_unauthorized_write:
            if should_escalate:
                raise HomeAssistantError(
                    "Carrier repeatedly rejected requests. "
                    "Check credentials or Carrier service health."
                ) from error
            raise HomeAssistantError(
                "Carrier temporarily rejected the request. Try again shortly."
            ) from error

        raise HomeAssistantError(
            "Carrier timed out while applying the request. Try again shortly."
        ) from error

    async def _async_reconcile_failed_write(
        self, operation_name: str, error: Exception | None = None
    ) -> None:
        """Refresh coordinator state after a write may have partially applied.

        Args:
            operation_name: Friendly name for the write operation that failed.
            error: Exception raised by the failed write, if available.
        """
        if isinstance(error, TransportServerError) and self._is_unauthorized_error(error):
            self.data_flush = True

        self._suppress_unauthorized_recording = True
        try:
            # A write may have partially applied server-side before the transport
            # failed, so force a refresh before reporting or re-raising upstream.
            await self.async_refresh()
        except (
            CarrierUnauthorizedError,
            HomeAssistantError,
            TimeoutError,
            TransportServerError,
            UpdateFailed,
        ) as refresh_error:  # pragma: no cover - defensive logging only
            _LOGGER.debug(
                "refresh after failed %s write failed: %s",
                operation_name,
                refresh_error,
            )
        finally:
            self._suppress_unauthorized_recording = False

    async def async_perform_api_call(
        self,
        operation_name: str,
        request: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Execute a Carrier write call with limited retry and recovery handling.

        Args:
            operation_name: Friendly name for the write operation, used in logs
                and user-facing error messages.
            request: Awaitable callback that performs the Carrier API write.

        Returns:
            Any: The result returned by the Carrier API request callback.

        Raises:
            HomeAssistantError: Raised after retry and refresh recovery are
                exhausted for retryable failures.
            HomeAssistantError: Raised after reconciliation for non-retryable
                request failures.
        """
        for attempt in range(MAX_WRITE_ATTEMPTS):
            try:
                result = await request()
            except (TransportServerError, TimeoutError) as error:
                if not self._is_retryable_write_error(error):
                    await self._async_reconcile_failed_write(operation_name, error)
                    raise HomeAssistantError(
                        "Failed to communicate with Carrier service — "
                        "operation could not be completed."
                    ) from error
                if await self._async_retry_write(attempt):
                    continue
                await self._async_handle_failed_write(operation_name, error)
                raise AssertionError("unreachable after failed write handling") from error
            else:
                self._reset_unauthorized_tracking()
                return result

        raise HomeAssistantError(
            "Carrier operation did not complete after the allowed retry attempts."
        )

    def system(self, system_serial: str) -> System | None:
        """Return the tracked system matching a Carrier serial.

        Args:
            system_serial: Carrier system serial to locate.

        Returns:
            System | None: Matching system object, or None when not found.
        """
        for system in self.systems:
            if system.profile.serial == system_serial:
                return system
        return None

    @staticmethod
    def mapped_system_data(system: System) -> str:
        """Return a stable mapped representation used for logging payloads.

        Args:
            system: Carrier system object to map.

        Returns:
            str: System mapping emitted by the Carrier model repr helper.
        """
        return system.__repr__()

    async def updated_callback(self, _message: str) -> None:
        """Handle websocket updates and notify Home Assistant listeners.

        Args:
            _message: Raw websocket payload string (unused after callback wiring).

        Returns:
            None: Listener state is refreshed in-place.
        """
        self.timestamp_websocket = datetime.now(UTC)
        _LOGGER.debug("websocket updated system")
        for system in self.systems:
            _LOGGER.debug(
                "%s",
                async_redact_data(self.mapped_system_data(system), TO_REDACT_MAPPED),
            )
        self.async_update_listeners()
