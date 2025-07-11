"""Update data from carrier api."""
from datetime import timedelta, datetime, UTC
from logging import Logger, getLogger


from carrier_api import ApiConnectionGraphql, System, Energy
from carrier_api.api_websocket_data_updater import WebsocketDataUpdater
from gql.transport.exceptions import TransportServerError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, \
    REQUEST_REFRESH_DEFAULT_COOLDOWN

from .const import DOMAIN, TO_REDACT_MAPPED
from .util import async_redact_data

_LOGGER: Logger = getLogger(__package__)
DEFAULT_UPDATE_INTERVAL_MINUTES = 30


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
            )
        )

    async def _async_update_data(self):
        try:
            if self.data_flush:
                _LOGGER.debug("fetching fresh all data")
                fresh_systems: list[System] = await self.api_connection.load_data()
                if self.systems is None:
                    self.systems = fresh_systems
                    self.websocket_data_updater = WebsocketDataUpdater(systems=self.systems)
                    self.api_connection.api_websocket.callback_add(self.websocket_data_updater.message_handler)
                    self.api_connection.api_websocket.callback_add(self.updated_callback)
                else:
                    for fresh_system in fresh_systems:
                        related_stale_system = self.system(fresh_system.profile.serial)
                        if related_stale_system is None:
                            _LOGGER.error(f"unable to find matching system, serial {fresh_system.profile.serial}")
                        else:
                            related_stale_system.profile = fresh_system.profile
                            related_stale_system.status = fresh_system.status
                            related_stale_system.config = fresh_system.config
                            related_stale_system.energy = fresh_system.energy
                for system in self.systems:
                    _LOGGER.debug(
                        async_redact_data(system.__repr__(), TO_REDACT_MAPPED)
                    )
                self.timestamp_all_data = datetime.now(UTC)
                self.timestamp_energy = self.timestamp_all_data
                self.data_flush = False
            else:
                _LOGGER.debug("fetching energy data")
                for system in self.systems:
                    energy_response = await self.api_connection.get_energy(system.profile.serial)
                    energy = Energy(raw=energy_response["infinityEnergy"])
                    system.energy = energy
                self.timestamp_energy = datetime.now(UTC)
            self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)
            return [system.__repr__() for system in self.systems]
        except TransportServerError as server_error:
            _LOGGER.exception(server_error)
            self.data_flush = True
            _LOGGER.debug("transport error likely carrier api maintenance so retrying in 1 minute.")
            self.update_interval = timedelta(minutes=1)
            raise UpdateFailed(server_error) from server_error
        except Exception as error:
            _LOGGER.exception(error)
            self.data_flush = True
            _LOGGER.debug("unrecognized error so retying in default 30 minutes but refreshing all data then.")
            raise UpdateFailed(error) from error

    def system(self, system_serial: str) -> System | None:
        for system in self.systems:
            if system.profile.serial == system_serial:
                return system

    async def updated_callback(self, _message: str) -> None:
        self.timestamp_websocket = datetime.now(UTC)
        _LOGGER.debug("websocket updated system")
        for system in self.systems:
            _LOGGER.debug(
                async_redact_data(system.__repr__(), TO_REDACT_MAPPED)
            )
        self.async_update_listeners()
