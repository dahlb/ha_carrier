"""Update data from carrier api."""

from logging import Logger, getLogger
from datetime import timedelta


from carrier_api import System, ApiConnection

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, TO_REDACT_MAPPED
from .util import async_redact_data

LOGGER: Logger = getLogger(__package__)


class CarrierDataUpdateCoordinator(DataUpdateCoordinator):
    """Update data from carrier api."""

    def __init__(
        self, hass: HomeAssistant, carrier_system: System, interval: int
    ) -> None:
        """Initialize the device."""
        self.hass: HomeAssistant = hass
        self.carrier_system: System = carrier_system
        self.api_connection: ApiConnection = carrier_system.api_connection

        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}-{self.carrier_system.name}",
            update_interval=timedelta(minutes=interval),
        )

    async def _async_update_data(self):
        try:
            await self.hass.async_add_executor_job(self.api_connection.activate)
            await self.hass.async_add_executor_job(self.carrier_system.status.refresh)
            await self.hass.async_add_executor_job(self.carrier_system.config.refresh)
            LOGGER.debug(
                async_redact_data(self.carrier_system.__repr__(), TO_REDACT_MAPPED)
            )
            return None
        except Exception as error:
            raise UpdateFailed(error) from error
