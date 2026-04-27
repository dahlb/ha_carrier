"""Shared entity base for Carrier systems and zones."""

import logging

from carrier_api import ConfigZone, StatusZone, System
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


class CarrierEntity(CoordinatorEntity[CarrierDataUpdateCoordinator]):
    """Provide common identity and data access for Carrier entities."""

    zone_api_id: str | None = None

    def __init__(
        self,
        entity_type: str,
        updater: CarrierDataUpdateCoordinator,
        context: str,
        **kwargs,
    ) -> None:
        """Initialize shared entity identity and coordinator context.

        Args:
            entity_type: Friendly suffix used in entity name and unique ID.
            updater: Coordinator that supplies Carrier system data.
            context: Carrier system serial used as coordinator context.
            **kwargs: Reserved for future entity initialization options.
        """
        super().__init__(updater, context)
        self._attr_name = f"{self.carrier_system.profile.name} {entity_type}"
        self._attr_unique_id = f"{self.carrier_system.profile.serial}_{entity_type}"

    @property
    def carrier_system(self) -> System:
        """Return the Carrier system bound to this entity context.

        Returns:
            System: Matching Carrier system instance.

        Raises:
            ValueError: Raised when the configured system serial is unknown.
        """
        csystem = self.coordinator.system(system_serial=self.coordinator_context)
        if csystem is None:
            raise ValueError(f"Carrier System not found: {self.coordinator_context}")
        return csystem

    @property
    def _status_zone(self) -> StatusZone:
        """Return status data for the zone associated with this entity.

        Returns:
            StatusZone: Runtime zone status from the latest coordinator payload.

        Raises:
            ValueError: Raised when zone metadata is missing or unresolved.
        """
        if self.zone_api_id is not None:
            for zone in self.carrier_system.status.zones:
                if zone.api_id == self.zone_api_id:
                    return zone
            raise ValueError(f"Status Zone not found: {self.zone_api_id}")
        raise ValueError("No zone api id defined")

    @property
    def _config_zone(self) -> ConfigZone:
        """Return configuration data for the zone associated with this entity.

        Returns:
            ConfigZone: Zone configuration from the latest coordinator payload.

        Raises:
            ValueError: Raised when zone metadata is missing or unresolved.
        """
        if self.zone_api_id is not None:
            for zone in self.carrier_system.config.zones:
                if zone.api_id == self.zone_api_id:
                    return zone
            raise ValueError(f"Config Zone not found: {self.zone_api_id}")
        raise ValueError("No zone api id defined")

    @property
    def device_info(self) -> DeviceInfo:
        """Build Home Assistant device metadata for this Carrier system.

        Returns:
            DeviceInfo: Device registry payload shared by all child entities.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self.carrier_system.profile.serial)},
            manufacturer=self.carrier_system.profile.brand,
            model=self.carrier_system.profile.model,
            sw_version=self.carrier_system.profile.firmware,
            name=self.carrier_system.profile.name,
        )
