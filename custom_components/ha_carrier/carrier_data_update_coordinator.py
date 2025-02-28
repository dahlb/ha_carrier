"""Update data from carrier api."""

from logging import Logger, getLogger


from carrier_api import ApiConnectionGraphql, System

from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, \
    REQUEST_REFRESH_DEFAULT_COOLDOWN

from .const import DOMAIN, TO_REDACT_MAPPED
from .util import async_redact_data

_LOGGER: Logger = getLogger(__package__)


class CarrierDataUpdateCoordinator(DataUpdateCoordinator):
    """Update data from carrier api."""

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
            update_interval=None,
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
            self.systems: list[System] = await self.api_connection.load_data()
            for system in self.systems:
                _LOGGER.debug(
                    async_redact_data(system.__repr__(), TO_REDACT_MAPPED)
                )
            return [system.__repr__() for system in self.systems]
        except Exception as error:
            _LOGGER.exception(error)
            raise UpdateFailed(error) from error

    def system(self, system_serial: str) -> System:
        for system in self.systems:
            if system.profile.serial == system_serial:
                return system
