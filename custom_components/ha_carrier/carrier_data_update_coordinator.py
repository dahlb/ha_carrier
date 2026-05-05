"""Coordinate polling, websocket updates, and writes for Carrier systems."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
import functools
import logging
from typing import Any, NoReturn

from carrier_api import ApiConnectionGraphql, AuthError, BaseError, Energy, System
from carrier_api.api_websocket_data_updater import WebsocketDataUpdater
from gql.transport.exceptions import TransportServerError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    MAX_REFRESH_ATTEMPTS,
    MAX_WRITE_ATTEMPTS,
    REFRESH_RETRY_BASE_DELAY_SECONDS,
    REFRESH_RETRY_MAX_DELAY_SECONDS,
    RETRY_JITTER_FRACTION,
    TO_REDACT_MAPPED,
    TRANSIENT_FAILURE_THRESHOLD,
    UNAUTHORIZED_RETRY_THRESHOLD,
    WRITE_RETRY_BASE_DELAY_SECONDS,
    WRITE_RETRY_MAX_DELAY_SECONDS,
)
from .exceptions import CarrierUnauthorizedError
from .resiliency import ResiliencyState, RetryPolicy, async_call_with_retry
from .util import (
    RECOVERABLE_REFRESH_EXCEPTIONS,
    RECOVERABLE_WRITE_COMMUNICATION_EXCEPTIONS,
    async_redact_data,
    is_unauthorized_error,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)

REFRESH_RETRY_POLICY = RetryPolicy(
    name="carrier-refresh",
    max_attempts=MAX_REFRESH_ATTEMPTS,
    base_delay=REFRESH_RETRY_BASE_DELAY_SECONDS,
    max_delay=REFRESH_RETRY_MAX_DELAY_SECONDS,
    jitter_fraction=RETRY_JITTER_FRACTION,
    retry_on_unauthorized=False,
)

WRITE_RETRY_POLICY = RetryPolicy(
    name="carrier-write",
    max_attempts=MAX_WRITE_ATTEMPTS,
    base_delay=WRITE_RETRY_BASE_DELAY_SECONDS,
    max_delay=WRITE_RETRY_MAX_DELAY_SECONDS,
    jitter_fraction=RETRY_JITTER_FRACTION,
    retry_on_unauthorized=True,
)

ENERGY_REFRESH_EXCEPTIONS: tuple[type[BaseException], ...] = (
    *RECOVERABLE_REFRESH_EXCEPTIONS,
    CarrierUnauthorizedError,
)


class CarrierDataUpdateCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
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
        self.resiliency = ResiliencyState(
            unauthorized_threshold=UNAUTHORIZED_RETRY_THRESHOLD,
            transient_threshold=TRANSIENT_FAILURE_THRESHOLD,
        )
        self.systems: list[System] = []
        self.websocket_data_updater: WebsocketDataUpdater | None = None
        self.websocket_task: asyncio.Task[None] | None = None
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

    async def _async_update_data(self) -> list[dict[str, Any]]:
        """Fetch the latest Carrier data via the centralized retry helper.

        Performs either a full system refresh or a lighter energy-only refresh,
        depending on whether the coordinator has been marked dirty. The shared
        `ResiliencyState` decides when 401s or transient failures escalate.

        Returns:
            list[dict[str, Any]]: List of mappings representing tracked systems.

        Raises:
            ConfigEntryAuthFailed: Raised when 401 responses cross the
                unauthorized threshold; HA prompts reauth.
            UpdateFailed: Raised when transient failures escalate or refresh
                cannot complete successfully for other reasons.
        """
        if self.resiliency.consecutive_unauthorized >= self.resiliency.unauthorized_threshold:
            self.data_flush = True
            self.update_interval = timedelta(minutes=1)
            raise ConfigEntryAuthFailed(
                "Carrier API rejected credentials; reauthentication required."
            )

        if self.data_flush:
            refresh_context = "full data refresh"
            refresh_operation = self._async_full_refresh
        else:
            refresh_context = "energy refresh"
            refresh_operation = self._async_energy_refresh

        try:
            await refresh_operation()
            return [self.mapped_system_data(system) for system in self.systems]
        except CarrierUnauthorizedError as error:
            self.data_flush = True
            self.update_interval = timedelta(minutes=1)
            raise ConfigEntryAuthFailed(
                "Carrier API rejected credentials; reauthentication required."
            ) from error
        except (
            asyncio.CancelledError,
            KeyboardInterrupt,
            SystemExit,
        ):
            raise
        except TransportServerError as error:
            self.data_flush = True
            self.update_interval = timedelta(minutes=1)
            if is_unauthorized_error(error):
                _LOGGER.info(
                    "Carrier %s returned unauthorized without crossing the reauth threshold.",
                    refresh_context,
                )
                raise UpdateFailed(
                    f"Carrier temporarily rejected {refresh_context}; will retry."
                ) from error
            _LOGGER.exception("Carrier %s failed", refresh_context, exc_info=error)
            raise UpdateFailed(
                f"Unexpected error during Carrier {refresh_context}: {error}"
            ) from error
        except RECOVERABLE_REFRESH_EXCEPTIONS as error:
            self.data_flush = True
            self.update_interval = timedelta(minutes=1)
            _LOGGER.exception("Carrier %s failed", refresh_context, exc_info=error)
            raise UpdateFailed(
                f"Unexpected error during Carrier {refresh_context}: {error}"
            ) from error

    async def _async_full_refresh(self) -> None:
        """Perform a full system refresh through the retry helper.

        Raises:
            CarrierUnauthorizedError: When 401s escalate beyond the threshold.
            BaseException: Any transient error that escalates beyond the threshold.
        """
        _LOGGER.debug("fetching fresh all data")
        fresh_systems: list[System] = await async_call_with_retry(
            self.api_connection.load_data,
            policy=REFRESH_RETRY_POLICY,
            state=self.resiliency,
            operation_name="full data refresh",
            logger=_LOGGER,
        )
        if not self.systems:
            self.systems = fresh_systems
        else:
            existing_by_serial = {s.profile.serial: s for s in self.systems}
            fresh_serials = {s.profile.serial for s in fresh_systems}

            for fresh_system in fresh_systems:
                existing = existing_by_serial.get(fresh_system.profile.serial)
                if existing is None:
                    _LOGGER.info(
                        "new system discovered, adding serial %s",
                        fresh_system.profile.serial,
                    )
                    self.systems.append(fresh_system)
                else:
                    existing.profile = fresh_system.profile
                    existing.status = fresh_system.status
                    existing.config = fresh_system.config
                    existing.energy = fresh_system.energy

            stale = [s for s in self.systems if s.profile.serial not in fresh_serials]
            for stale_system in stale:
                _LOGGER.info(
                    "system no longer present in Carrier account, removing serial %s",
                    stale_system.profile.serial,
                )
                self.systems.remove(stale_system)
        if not self._websocket_initialized:
            self.websocket_data_updater = WebsocketDataUpdater(systems=self.systems)
            self.api_connection.api_websocket.callback_add(
                self.websocket_data_updater.message_handler
            )
            self.api_connection.api_websocket.callback_add(self.updated_callback)
            self._websocket_initialized = True
        if _LOGGER.isEnabledFor(logging.DEBUG):
            for system in self.systems:
                _LOGGER.debug(
                    "%s",
                    async_redact_data(self.mapped_system_data(system), TO_REDACT_MAPPED),
                )
        self.timestamp_all_data = datetime.now(UTC)
        self.timestamp_energy = self.timestamp_all_data
        self.data_flush = False
        self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

    async def _async_energy_refresh(self) -> None:
        """Perform an energy-only refresh through the retry helper.

        A 401 from any one system is treated like the previous behavior:
        preserve last known energy data and bump the shared unauthorized counter
        once for the whole cycle. The helper still owns transient retry /
        backoff behavior per system.

        Raises:
            CarrierUnauthorizedError: When 401s escalate beyond the threshold.
        """
        _LOGGER.debug("fetching energy data")
        found_unauthorized = False
        for system in self.systems:
            try:
                energy_response = await async_call_with_retry(
                    functools.partial(self.api_connection.get_energy, system.profile.serial),
                    policy=REFRESH_RETRY_POLICY,
                    state=self.resiliency,
                    operation_name="energy refresh",
                    logger=_LOGGER,
                    manage_unauthorized_state=False,
                    reset_unauthorized_on_success=False,
                )
            except ENERGY_REFRESH_EXCEPTIONS as error:
                if not is_unauthorized_error(error):
                    raise
                found_unauthorized = True
                continue
            energy = Energy(raw=energy_response["infinityEnergy"])
            system.energy = energy
        if found_unauthorized:
            self.update_interval = timedelta(minutes=1)
            if self.resiliency.record_unauthorized(_LOGGER, "energy refresh cycle"):
                raise CarrierUnauthorizedError(
                    "Carrier API repeatedly rejected energy refresh requests."
                )
        else:
            self.resiliency.reset_unauthorized()
            self.timestamp_energy = datetime.now(UTC)
            self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

    async def _async_handle_failed_write(
        self,
        operation_name: str,
        error: Exception,
    ) -> NoReturn:
        """Recover from a failed write and raise a user-facing Home Assistant error.

        Forces a refresh so entity state is reconciled with the Carrier backend
        before surfacing the failure to the user.

        Args:
            operation_name: Friendly name for the write operation that failed.
            error: Exception raised by the Carrier API client. Either a
                CarrierUnauthorizedError (already escalated by the helper) or
                a TimeoutError that exhausted retries.

        Raises:
            HomeAssistantError: Raised with a message tailored to the failure type.
        """
        is_unauthorized = isinstance(error, CarrierUnauthorizedError)
        await self._async_reconcile_failed_write(operation_name, error)

        if is_unauthorized:
            # The helper already crossed the threshold before raising; the next
            # refresh tick will see the same counter and raise
            # ConfigEntryAuthFailed, which triggers HA's reauth flow.
            raise HomeAssistantError(
                "Carrier repeatedly rejected requests. Reauthentication will be triggered shortly."
            ) from error

        raise HomeAssistantError(
            "Carrier timed out while applying the request. Try again shortly."
        ) from error

    async def _async_reconcile_failed_write(
        self, operation_name: str, error: BaseException | None = None
    ) -> None:
        """Refresh coordinator state after a write may have partially applied.

        Args:
            operation_name: Friendly name for the write operation that failed.
            error: Exception raised by the failed write, if available.
        """
        if error is not None and is_unauthorized_error(error):
            self.data_flush = True

        prev_suppress = self.resiliency.suppress_recording
        if prev_suppress:  # pragma: no cover - defensive logging only
            _LOGGER.debug(
                "refresh after failed %s write is already suppressing resiliency recording",
                operation_name,
            )

        self.resiliency.suppress_recording = True
        try:
            # A write may have partially applied server-side before the transport
            # failed, so force a refresh before reporting or re-raising upstream.
            if isinstance(error, CarrierUnauthorizedError):
                with self.resiliency.preserve_unauthorized():
                    await self.async_refresh()
                self.data_flush = True
                self.update_interval = timedelta(minutes=1)
            else:
                await self.async_refresh()
        except (
            CarrierUnauthorizedError,
            ConfigEntryAuthFailed,
            HomeAssistantError,
            UpdateFailed,
        ) as refresh_error:
            # pragma: no cover - defensive logging only
            _LOGGER.debug(
                "refresh after failed %s write failed: %s",
                operation_name,
                refresh_error,
            )
        finally:
            self.resiliency.suppress_recording = prev_suppress

    async def async_perform_api_call(
        self,
        operation_name: str,
        request: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Execute a Carrier write call via the centralized retry helper.

        Args:
            operation_name: Friendly name for the write operation, used in logs
                and user-facing error messages.
            request: Awaitable callback that performs the Carrier API write.

        Returns:
            Any: The result returned by the Carrier API request callback.

        Raises:
            HomeAssistantError: Raised after retry and refresh recovery are
                exhausted for retryable failures or for non-retryable failures.
        """
        try:
            return await async_call_with_retry(
                request,
                policy=WRITE_RETRY_POLICY,
                state=self.resiliency,
                operation_name=operation_name,
                logger=_LOGGER,
            )
        except CarrierUnauthorizedError as error:
            await self._async_handle_failed_write(operation_name, error)
            raise AssertionError("unreachable after failed write handling") from error
        except (
            asyncio.CancelledError,
            KeyboardInterrupt,
            SystemExit,
        ):
            raise
        except TimeoutError as error:
            await self._async_handle_failed_write(operation_name, error)
            raise AssertionError("unreachable after failed write handling") from error
        except RECOVERABLE_WRITE_COMMUNICATION_EXCEPTIONS as error:
            await self._async_reconcile_failed_write(operation_name, error)
            raise HomeAssistantError(
                "Failed to communicate with Carrier service — operation could not be completed."
            ) from error
        except (AuthError, BaseError) as error:
            await self._async_reconcile_failed_write(operation_name, error)
            raise HomeAssistantError(
                "Carrier rejected the request. Check the requested setting and try again."
            ) from error

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
    def mapped_system_data(system: System) -> dict[str, Any]:
        """Return a stable mapped representation used for logging payloads.

        Args:
            system: Carrier system object to map.

        Returns:
            dict[str, Any]: System mapping emitted by the Carrier model helper.

        Raises:
            TypeError: Raised when the Carrier model returns a non-mapping
                payload unexpectedly.
        """
        # carrier_api.System.__repr__ intentionally returns a dict-like payload,
        # while Python's built-in repr(system) expects __repr__ to return a str.
        # Call the model helper directly so we keep a mapping for recursive
        # redaction instead of flattening sensitive keys into an opaque string.
        mapped_data = system.__repr__()
        if not isinstance(mapped_data, Mapping):
            raise TypeError("carrier_api System.__repr__ returned a non-mapping payload")
        return dict(mapped_data)

    async def updated_callback(self, _message: str) -> None:
        """Handle websocket updates and notify Home Assistant listeners.

        Args:
            _message: Raw websocket payload string (unused after callback wiring).

        Returns:
            None: Listener state is refreshed in-place.
        """
        self.timestamp_websocket = datetime.now(UTC)
        _LOGGER.debug("websocket updated system")
        if _LOGGER.isEnabledFor(logging.DEBUG):
            for system in self.systems:
                _LOGGER.debug(
                    "%s",
                    async_redact_data(self.mapped_system_data(system), TO_REDACT_MAPPED),
                )
        self.async_update_listeners()
