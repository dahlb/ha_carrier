"""Coordinate polling, websocket updates, and writes for Carrier systems."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
import functools
import logging
from typing import Any, NoReturn

from aiohttp import ClientError
from carrier_api import ApiConnectionGraphql, CarrierApiError, Energy, EntryLevelSystem, System
from carrier_api.api_websocket_data_updater import WebsocketDataUpdater
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    FULL_RECONCILE_INTERVAL_MINUTES,
    MAX_REFRESH_ATTEMPTS,
    MAX_WRITE_ATTEMPTS,
    POST_WRITE_INTERCEPT_WINDOW_MINUTES,
    RECONCILE_BACKGROUND_INTERVAL_SECONDS,
    RECONCILE_BURST_DELAYS_SECONDS,
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
    TRANSIENT_TRANSPORT_EXCEPTIONS,
    async_redact_data,
    is_unauthorized_error,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)

FULL_RECONCILE_INTERVAL = timedelta(minutes=FULL_RECONCILE_INTERVAL_MINUTES)
POST_WRITE_INTERCEPT_WINDOW = timedelta(minutes=POST_WRITE_INTERCEPT_WINDOW_MINUTES)

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

# Failures a reconcile send may hit: raw websocket transport errors from
# aiohttp plus the carrier_api transient wrappers. A reconcile is a best-effort
# nudge, so these are logged and swallowed — the next scheduled send retries.
RECONCILE_SEND_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ClientError,
    TimeoutError,
    OSError,
    *TRANSIENT_TRANSPORT_EXCEPTIONS,
)


class CarrierDataUpdateCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Maintain Carrier data and shared API resiliency state for one account."""

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
        self.entry_level_systems: list[EntryLevelSystem] = []
        self.websocket_data_updater: WebsocketDataUpdater | None = None
        self.websocket_task: asyncio.Task[None] | None = None
        self._websocket_initialized = False
        self.data_flush = True
        self.timestamp_all_data: datetime | None = None
        self.timestamp_websocket: datetime | None = None
        self.timestamp_energy: datetime | None = None
        self._intercept_guards: dict[tuple[str, str | None], dict[str, Any]] = {}
        self._reconcile_tick_unsub: Callable[[], None] | None = None
        self._reconcile_burst_unsub: Callable[[], None] | None = None
        self._reconcile_burst_index: int = 0

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

    def start_reconcile_schedule(self) -> None:
        """Start the periodic websocket reconcile tick. Idempotent.

        Carrier's realtime push stream is at-most-once: a dropped delta, or a
        stale post-write snapshot delivered after the genuine transition it
        predates, leaves a status field frozen with nothing to correct it while
        the compressor runs steadily (the cloud only pushes edges). The stream
        carries no sequence numbers and stale snapshots are re-stamped with
        fresh timestamps, so a wrong value cannot be detected — only outlived.
        The tick sends ``{"action": "reconcile"}`` on the open socket every
        ``RECONCILE_BACKGROUND_INTERVAL_SECONDS`` so the cloud re-pushes current
        state, bounding how long any silently stale field can survive.
        """
        if self._reconcile_tick_unsub is None:
            self._reconcile_tick_unsub = async_track_time_interval(
                self.hass,
                self._async_reconcile_tick,
                timedelta(seconds=RECONCILE_BACKGROUND_INTERVAL_SECONDS),
            )

    def stop_reconcile_schedule(self) -> None:
        """Stop the periodic tick and cancel any pending burst step."""
        if self._reconcile_tick_unsub is not None:
            self._reconcile_tick_unsub()
            self._reconcile_tick_unsub = None
        self._cancel_reconcile_burst()

    def _cancel_reconcile_burst(self) -> None:
        """Cancel the pending burst step, if one is scheduled."""
        if self._reconcile_burst_unsub is not None:
            self._reconcile_burst_unsub()
            self._reconcile_burst_unsub = None

    def schedule_reconcile_burst(self) -> None:
        """(Re)start the post-write reconcile burst.

        Right after a write the cloud's read model lags the physical unit, so
        its pushes — including the post-write snapshot the API layer already
        requests — can restate pre-write status that overwrites a genuine
        transition (for example a zone stuck ``idle`` while the compressor
        runs). The burst re-asks on ``RECONCILE_BURST_DELAYS_SECONDS`` backoff:
        early steps converge the UI quickly when the cloud is fast, the later
        steps land after the cloud's measured worst-case lag and after
        ``POST_WRITE_INTERCEPT_WINDOW_MINUTES`` expires, re-pulling truth once
        the post-write guard stops re-asserting. A newer write restarts the
        burst; its race window supersedes the old one.
        """
        self._cancel_reconcile_burst()
        self._reconcile_burst_index = 0
        self._schedule_next_burst_step()

    def _schedule_next_burst_step(self) -> None:
        """Schedule the burst step at the current backoff delay."""
        delay = RECONCILE_BURST_DELAYS_SECONDS[self._reconcile_burst_index]
        self._reconcile_burst_unsub = async_call_later(self.hass, delay, self._async_burst_step)

    async def _async_burst_step(self, _now: datetime) -> None:
        """Send one burst reconcile and schedule the next step, if any.

        Only ``RECONCILE_SEND_EXCEPTIONS`` are swallowed by the send. Anything
        else is a genuine bug and deliberately propagates, abandoning the rest
        of this burst; the periodic tick still recovers state on its cadence.
        """
        self._reconcile_burst_unsub = None
        await self._async_send_reconcile()
        self._reconcile_burst_index += 1
        if self._reconcile_burst_index < len(RECONCILE_BURST_DELAYS_SECONDS):
            self._schedule_next_burst_step()

    async def _async_reconcile_tick(self, _now: datetime) -> None:
        """Send the periodic background reconcile."""
        await self._async_send_reconcile()

    async def _async_send_reconcile(self) -> None:
        """Ask Carrier to re-push current state over the websocket.

        Best-effort: skipped quietly when the websocket client does not exist
        yet, and transport failures are logged at debug and swallowed — the
        next scheduled send is the retry. A dead socket is already handled by
        the websocket task's own full-refresh recovery path.
        """
        api_websocket = self.api_connection.api_websocket
        if api_websocket is None:
            _LOGGER.debug("reconcile skipped: websocket client not initialized")
            return
        try:
            await api_websocket.send_reconcile()
        except RECONCILE_SEND_EXCEPTIONS as error:
            _LOGGER.debug("reconcile send failed; next scheduled send retries: %s", error)

    def _full_reconcile_due(self) -> bool:
        """Return True when websocket-maintained data is overdue for a full fetch.

        In steady state the poll cycle only refreshes energy and relies on
        websocket deltas for status. A delta that is dropped or rebroadcast stale
        leaves a status field frozen with no self-correction while the socket
        stays connected. A periodic full refresh reconciles that state against an
        authoritative pull.

        Returns:
            bool: True when the last full refresh is older than
                ``FULL_RECONCILE_INTERVAL`` or has never completed.
        """
        if self.timestamp_all_data is None:
            return True
        return datetime.now(UTC) - self.timestamp_all_data >= FULL_RECONCILE_INTERVAL

    def begin_post_write_intercept(
        self, system_serial: str, zone_api_id: str | None = None
    ) -> None:
        """Open (or refresh) a post-write guard for the system (and zone) just written.

        Carrier's cloud can replay the pre-write snapshot over the websocket
        (a fast bounce within seconds, or a slow revert ~2 min later), so for a
        window after each successful write the coordinator re-asserts any control
        field (mode / set point) the cloud reverts *on the written target* back to
        the intended value, while still publishing every other field, zone, and
        system. See ``updated_callback``.

        Guards are tracked independently, keyed by ``(system_serial, zone_api_id)``
        with a system-level mode guard keyed by ``(system_serial, None)``. Each
        guard carries its own expiry, so concurrent writes to different systems or
        zones — a second thermostat, or this integration's own ``climate.turn_off``
        auto-shutoff writing a different system — each keep their own protection for
        their own five minutes, and a later write to one target never extends
        another target's guard. A repeat write to the same target upserts it.

        The system mode guard is re-stamped to the current mode on every write to
        the system, so it never holds a stale mode; each zone guard freezes that
        zone's intended set point and expires on its own clock.

        Called by the writing entity from ``_write_local_state`` *after* it has
        applied its optimistic local mutation, so the snapshot captures the intended
        post-write control state rather than the pre-write state.

        Args:
            system_serial: Serial of the system the entity wrote.
            zone_api_id: Zone written, or None for a system-level write (mode).
        """
        system = self.system(system_serial)
        if system is None:
            return
        expires_at = datetime.now(UTC) + POST_WRITE_INTERCEPT_WINDOW
        self._intercept_guards[(system_serial, None)] = {
            "expires_at": expires_at,
            "mode": system.config.mode,
        }
        if zone_api_id is not None:
            zone_state = self._capture_zone_state(system, zone_api_id)
            if zone_state is not None:
                self._intercept_guards[(system_serial, zone_api_id)] = {
                    "expires_at": expires_at,
                    **zone_state,
                }

    def _capture_zone_state(self, system: System, zone_api_id: str) -> dict[str, Any] | None:
        """Return the intended activity type and resolved set points for one zone.

        Resolves the set point the same way the climate entity reads it back
        (``config_zone.current_status_activity(status_zone) or status_zone``) so a
        later re-assert compares like with like. Volatile status (temperature,
        humidity, conditioning, blower) is deliberately excluded so a normal reading
        update is never mistaken for a reverted write.
        """
        status_zone, config_zone = self._zone_pair(system, zone_api_id)
        if status_zone is None or config_zone is None:
            return None
        source = config_zone.current_status_activity(status_zone) or status_zone
        return {
            "activity_type": status_zone.current_status_activity_type,
            "heat_set_point": source.heat_set_point,
            "cool_set_point": source.cool_set_point,
        }

    def _prune_intercept_guards(self) -> None:
        """Drop post-write guards whose own protection period has elapsed."""
        now = datetime.now(UTC)
        expired = [
            key for key, guard in self._intercept_guards.items() if now >= guard["expires_at"]
        ]
        for key in expired:
            del self._intercept_guards[key]

    def _in_post_write_intercept(self) -> bool:
        """Return True while any written target still has a live post-write guard."""
        self._prune_intercept_guards()
        return bool(self._intercept_guards)

    def _zone_pair(self, system: System, zone_api_id: str) -> tuple[Any, Any] | tuple[None, None]:
        """Return the (status_zone, config_zone) pair for a zone id, or (None, None)."""
        status_zone = next(
            (zone for zone in system.status.zones if zone.api_id == zone_api_id), None
        )
        config_zone = next(
            (zone for zone in system.config.zones if zone.api_id == zone_api_id), None
        )
        if status_zone is None or config_zone is None:
            return None, None
        return status_zone, config_zone

    def _reassert_control(self) -> None:
        """Restore reverted control fields on every written target with a live guard.

        Runs after ``message_handler`` has applied a websocket message. Only each
        guarded target — a written system's mode, or a written zone's set point — is
        considered; every other system, zone, and non-control field is left exactly
        as delivered, so a revert on a guarded target never drops — or reverts —
        another target's update. Does not read the API. When Carrier finally accepts
        a write its value matches the snapshot and nothing is rewritten; when a
        guard expires that target is trusted again.
        """
        self._prune_intercept_guards()
        for (system_serial, zone_api_id), guard in self._intercept_guards.items():
            system = self.system(system_serial)
            if system is None:
                continue
            if zone_api_id is None:
                if system.config.mode != guard["mode"]:
                    system.config.mode = guard["mode"]
            else:
                self._reassert_zone(system, zone_api_id, guard)

    def _reassert_zone(self, system: System, zone_api_id: str, zone_state: dict[str, Any]) -> None:
        """Restore one written zone's reverted activity type and set points."""
        status_zone, config_zone = self._zone_pair(system, zone_api_id)
        if status_zone is None or config_zone is None:
            return
        source = config_zone.current_status_activity(status_zone) or status_zone
        if (
            source.heat_set_point == zone_state["heat_set_point"]
            and source.cool_set_point == zone_state["cool_set_point"]
            and status_zone.current_status_activity_type == zone_state["activity_type"]
        ):
            return
        status_zone.current_status_activity_type = zone_state["activity_type"]
        status_zone.heat_set_point = zone_state["heat_set_point"]
        status_zone.cool_set_point = zone_state["cool_set_point"]
        reasserted = config_zone.find_activity(zone_state["activity_type"])
        if reasserted is not None:
            reasserted.heat_set_point = zone_state["heat_set_point"]
            reasserted.cool_set_point = zone_state["cool_set_point"]

    async def _async_update_data(self) -> list[dict[str, Any]]:
        """Fetch Carrier data and translate escalated failures for Home Assistant.

        Performs either a full system refresh or a lighter energy-only refresh,
        depending on whether the coordinator has been marked dirty. The shared
        `ResiliencyState` tracks 401 and transient failures across API calls.
        Unauthorized failures only become `ConfigEntryAuthFailed` after a fresh
        refresh attempt fails and crosses the shared threshold, so a later
        successful refresh can still clear an old outage window. Non-escalated
        refresh failures remain `UpdateFailed`, which lets Home Assistant retry
        later.

        Returns:
            list[dict[str, Any]]: List of mappings representing tracked systems.

        Raises:
            ConfigEntryAuthFailed: Raised when 401 responses cross the
                unauthorized threshold; HA prompts reauth.
            UpdateFailed: Raised when transient failures escalate or refresh
                cannot complete successfully for other reasons.
        """
        if not self.data_flush and self._full_reconcile_due():
            _LOGGER.debug(
                "forcing full refresh: last full reconcile was >= %s minutes ago",
                FULL_RECONCILE_INTERVAL_MINUTES,
            )
            self.data_flush = True

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
        except RECOVERABLE_REFRESH_EXCEPTIONS as error:
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

    async def _async_full_refresh(self) -> None:
        """Load all Carrier systems through the normal retry path.

        A successful full refresh represents a healthy API round trip, so the
        retry helper uses its default behavior and resets shared resiliency
        counters. System objects are then updated in place so websocket callbacks
        and entity references keep pointing at the live coordinator list.

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
            self.systems.clear()
            self.systems.extend(fresh_systems)
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
        await self._refresh_entry_level_systems()
        if not self._websocket_initialized:
            self.websocket_data_updater = WebsocketDataUpdater(systems=self.systems)
            api_websocket = self.api_connection.api_websocket
            if api_websocket is None:
                raise RuntimeError("Carrier API websocket client is not initialized")
            api_websocket.callback_add(self.websocket_data_updater.message_handler)
            api_websocket.callback_add(self.updated_callback)
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
        # A full read is authoritative; end every post-write guard so re-assert
        # cannot fight freshly-read truth.
        self._intercept_guards = {}

    async def _async_energy_refresh(self) -> None:
        """Refresh energy data while accounting for failures once per cycle.

        Energy refresh calls the API once per system, but all systems together
        are one logical coordinator refresh. A 401 from any system preserves that
        system's previous energy payload and immediately records one unauthorized
        failure for the whole cycle. Per-system helper successes use
        `reset_state_on_success=False` so a later successful system cannot erase
        failure evidence from an earlier system in the same cycle.

        The helper still owns per-system transient retry and backoff. A fully
        successful energy cycle clears unauthorized tracking and restores the
        normal polling interval.

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
                    reset_state_on_success=False,
                )
            except ENERGY_REFRESH_EXCEPTIONS as error:
                if not is_unauthorized_error(error):
                    raise
                if not found_unauthorized:
                    found_unauthorized = True
                    self.update_interval = timedelta(minutes=1)
                    if self.resiliency.record_unauthorized(_LOGGER, "energy refresh cycle"):
                        raise CarrierUnauthorizedError(
                            "Carrier API repeatedly rejected energy refresh requests."
                        ) from error
                continue
            energy = Energy(raw=energy_response["infinityEnergy"])
            system.energy = energy
        if not found_unauthorized:
            self.resiliency.reset_unauthorized()
            self.resiliency.reset_transient()
            self.timestamp_energy = datetime.now(UTC)
            self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

    async def _async_handle_failed_write(
        self,
        operation_name: str,
        error: CarrierUnauthorizedError,
    ) -> NoReturn:
        """Recover from an exhausted unauthorized write and raise a HA error.

        Forces a refresh so entity state is reconciled with the Carrier backend
        before surfacing the failure to the user. If reconciliation succeeds,
        that successful refresh clears the shared counters. If credentials are
        still rejected, the reconciliation refresh or a later scheduled refresh
        will record a fresh unauthorized failure and trigger reauthentication.

        Args:
            operation_name: Friendly name for the write operation that failed.
            error: Unauthorized error raised after retry handling.

        Raises:
            HomeAssistantError: Raised with a retry-later message.
        """
        await self._async_reconcile_failed_write(operation_name, error)

        # The write crossed the auth threshold, but reconciliation may have
        # already cleared stale counters. Reauth is left to a fresh failed
        # refresh so one recovered write outage does not force credentials
        # invalid.
        raise HomeAssistantError(
            "Carrier rejected repeated write attempts. Try again shortly."
        ) from error

    async def _async_reconcile_failed_write(
        self, operation_name: str, error: BaseException | None = None
    ) -> None:
        """Refresh coordinator state after a write may have partially applied.

        Carrier can accept a write server-side and still fail the client request
        because of a timeout or transport interruption. Reconciliation forces a
        normal coordinator refresh before the user-facing error is raised. That
        refresh owns its own retry accounting: success clears stale counters,
        while continued failures record fresh evidence.

        Args:
            operation_name: Friendly name for the write operation that failed.
            error: Exception raised by the failed write, if available.
        """
        if error is not None and (
            is_unauthorized_error(error)
            or isinstance(error, RECOVERABLE_WRITE_COMMUNICATION_EXCEPTIONS)
        ):
            self.data_flush = True

        try:
            # Refresh may repair local state after a server-side partial write.
            # It also provides the fresh success/failure signal used for counters.
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
            result = await async_call_with_retry(
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
        except RECOVERABLE_WRITE_COMMUNICATION_EXCEPTIONS as error:
            await self._async_reconcile_failed_write(operation_name, error)
            raise HomeAssistantError(
                "Failed to communicate with Carrier service — operation could not be completed."
            ) from error
        except CarrierApiError as error:
            await self._async_reconcile_failed_write(operation_name, error)
            raise HomeAssistantError(
                "Carrier rejected the request. Check the requested setting and try again."
            ) from error
        # Successful write: the cloud's read model now lags the unit, so start
        # the reconcile burst to converge websocket-maintained status quickly.
        self.schedule_reconcile_burst()
        return result

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

    async def _refresh_entry_level_systems(self) -> None:
        """Refresh entry-level (Smart Thermostat) systems as a best-effort step.

        Entry-level data is independent of the Infinity systems and websocket
        path, so a failure here is logged and swallowed rather than failing the
        whole refresh.
        """
        try:
            self.entry_level_systems = await self.api_connection.load_entry_level_data()
        except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
            raise
        except (CarrierApiError, *RECOVERABLE_REFRESH_EXCEPTIONS) as error:
            _LOGGER.debug("Carrier entry-level refresh failed (non-fatal): %s", error)

    def entry_level_system(self, serial: str) -> EntryLevelSystem | None:
        """Return the tracked entry-level system matching a serial.

        Args:
            serial: Entry-level system serial to locate.

        Returns:
            EntryLevelSystem | None: Matching system, or None when not found.
        """
        for system in self.entry_level_systems:
            if system.serial == serial:
                return system
        return None

    @staticmethod
    def mapped_system_data(system: System) -> dict[str, Any]:
        """Return a stable mapped representation used for logging payloads.

        Args:
            system: Carrier system object to map.

        Returns:
            dict[str, Any]: System mapping emitted by the Carrier model serializer.

        Raises:
            TypeError: Raised when the Carrier model returns a non-mapping
                payload unexpectedly.
        """
        mapped_data = system.as_dict()
        if not isinstance(mapped_data, Mapping):
            raise TypeError("carrier_api System serializer returned a non-mapping payload")
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
        if self._in_post_write_intercept():
            # Re-assert any control field (mode / set point) the cloud reverted
            # back to the intended post-write value. The message is still
            # published normally below, so every other field and zone in the
            # same websocket message reaches Home Assistant.
            self._reassert_control()
        self.async_update_listeners()
