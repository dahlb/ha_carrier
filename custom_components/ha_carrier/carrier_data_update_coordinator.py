import logging
from datetime import timedelta


from carrier_api import System, ApiConnection

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class CarrierDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, carrier_system: System, interval: int) -> None:
        """Initialize the device."""
        self.hass: HomeAssistant = hass
        self.carrier_system: System = carrier_system
        self.api_connection: ApiConnection = carrier_system.api_connection

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.carrier_system.name}",
            update_interval=timedelta(minutes=interval),
        )

    async def _async_update_data(self):
        try:
            await self.hass.async_add_executor_job(self.api_connection.activate)
            await self.hass.async_add_executor_job(self.carrier_system.status.refresh)
            await self.hass.async_add_executor_job(self.carrier_system.config.refresh)
            _LOGGER.debug(self.carrier_system)
            return None
        except Exception as error:
            raise UpdateFailed(error) from error
