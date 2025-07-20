"""Base entity for carrier devices."""
from logging import getLogger, Logger

from carrier_api import StatusZone, ConfigZone, System
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import DOMAIN
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator

_LOGGER: Logger = getLogger(__package__)


class CarrierEntity(CoordinatorEntity[CarrierDataUpdateCoordinator]):
    """Base entity for carrier devices."""
    def __init__(
            self,
            entity_type: str,
            updater: CarrierDataUpdateCoordinator,
            context: str,
            **kwargs,
    ) -> None:
        """Create unique_id and access to api data."""
        super().__init__(updater, context)
        self._attr_name = f"{self.carrier_system.profile.name} {entity_type}"
        self._attr_unique_id = f"{self.carrier_system.profile.serial}_{entity_type}"

    @property
    def carrier_system(self) -> System:
        return self.coordinator.system(system_serial=self.coordinator_context)

    @property
    def _status_zone(self) -> StatusZone:
        if getattr(self, "zone_api_id", None) is not None:
            for zone in self.carrier_system.status.zones:
                if zone.api_id == self.zone_api_id:
                    return zone
            raise ValueError(f"Status Zone not found: {self.zone_api_id}")
        else:
            raise ValueError("No zone api id defined")

    @property
    def _config_zone(self) -> ConfigZone:
        if getattr(self, "zone_api_id", None) is not None:
            for zone in self.carrier_system.config.zones:
                if zone.api_id == self.zone_api_id:
                    return zone
            raise ValueError(f"Config Zone not found: {self.zone_api_id}")
        else:
            raise ValueError("No zone api id defined")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.carrier_system.profile.serial)},
            manufacturer=self.carrier_system.profile.brand,
            model=self.carrier_system.profile.model,
            sw_version=self.carrier_system.profile.firmware,
            name=self.carrier_system.profile.name,
        )
