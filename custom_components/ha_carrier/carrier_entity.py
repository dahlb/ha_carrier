"""Base entity for carrier devices."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import DOMAIN
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator


class CarrierEntity(CoordinatorEntity):
    """Base entity for carrier devices."""

    _attr_force_update = False
    _attr_should_poll = False

    def __init__(
        self,
        entity_type: str,
        updater: CarrierDataUpdateCoordinator,
        **kwargs,
    ) -> None:
        """Create unique_id and access to api data."""
        super().__init__(updater)
        self._updater: CarrierDataUpdateCoordinator = updater
        self._attr_name = f"{self._updater.carrier_system.name} {entity_type}"
        self._attr_unique_id = f"{self._updater.carrier_system.serial}_{entity_type}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._updater.carrier_system.serial)},
            manufacturer="Carrier",
            model=self._updater.carrier_system.profile.model,
            sw_version=self._updater.carrier_system.profile.firmware,
            name=self._updater.carrier_system.name,
        )

    async def async_update(self):
        """Update Blueair entity."""
        await self._updater.async_request_refresh()

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(
            self._updater.async_add_listener(self.async_write_ha_state)
        )
