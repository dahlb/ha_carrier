"""Base entity for carrier devices."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import DOMAIN
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator


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
    def carrier_system(self):
        return self.coordinator.system(system_serial=self.coordinator_context)

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
