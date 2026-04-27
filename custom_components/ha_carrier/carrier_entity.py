"""Shared entity base for Carrier systems and zones."""

import logging

from carrier_api import ConfigZone, StatusZone, System
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


class CarrierEntity(CoordinatorEntity[CarrierDataUpdateCoordinator]):
    """Provide common identity and data access for Carrier entities."""

    _attr_has_entity_name: bool = True
    zone_api_id: str | None = None

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize shared entity identity and coordinator context.

        Args:
            entity_name: Friendly suffix used in entity name and unique ID.
            coordinator: Coordinator that supplies Carrier system data.
            system_serial: Carrier system serial used as coordinator context.
            unique_id_suffix: Optional stable suffix used for the entity unique ID.
        """
        super().__init__(coordinator, system_serial)
        self._system_serial = system_serial
        self._attr_name = f"{entity_name}"
        unique_id_raw = f"{system_serial}_{unique_id_suffix or entity_name}"
        self._attr_unique_id = slugify(unique_id_raw)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.carrier_system.profile.serial)},
            manufacturer=self.carrier_system.profile.brand,
            model=self.carrier_system.profile.model,
            sw_version=self.carrier_system.profile.firmware,
            name=self.carrier_system.profile.name,
        )

    def _update_entity_attrs(self) -> None:
        """Update Home Assistant attrs from the latest coordinator data.

        Subclasses should populate their supported ``self._attr_*`` values here.
        """

    def _sync_entity_attrs(self) -> None:
        """Refresh entity attrs from coordinator data with shared error handling."""
        try:
            self._update_entity_attrs()
        except ValueError as error:
            _LOGGER.debug(
                "Unable to update Carrier entity %s: %s",
                self._attr_unique_id,
                error,
            )
            self._attr_available = False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh entity attrs after a coordinator update."""
        self._sync_entity_attrs()
        super()._handle_coordinator_update()

    @callback
    def _write_local_state(self) -> None:
        """Refresh attrs after local state mutation and write them to Home Assistant."""
        self._sync_entity_attrs()
        self.async_write_ha_state()

    @property
    def carrier_system(self) -> System:
        """Return the Carrier system bound to this entity context.

        Returns:
            System: Matching Carrier system instance.

        Raises:
            ValueError: Raised when the configured system serial is unknown.
        """
        csystem = self.coordinator.system(system_serial=self._system_serial)
        if csystem is None:
            raise ValueError(f"Carrier System not found: {self._system_serial}")
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


class CarrierZoneEntity(CarrierEntity):
    """Shared Carrier entity base for entities bound to a specific zone."""

    zone_name: str | None = None

    @staticmethod
    def resolve_zone_name(
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        zone_api_id: str,
    ) -> str:
        """Return the configured name for a Carrier zone.

        Args:
            coordinator: Coordinator that supplies Carrier system data.
            system_serial: Carrier system serial used as coordinator context.
            zone_api_id: Carrier API identifier for the represented zone.

        Returns:
            str: Configured zone name.

        Raises:
            ValueError: Raised when the system or zone cannot be resolved.
        """
        carrier_system = coordinator.system(system_serial=system_serial)
        if carrier_system is None:
            raise ValueError(f"Carrier System not found: {system_serial}")
        for zone in carrier_system.config.zones:
            if zone.api_id == zone_api_id:
                return zone.name
        raise ValueError(f"Config Zone not found: {zone_api_id}")

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        zone_api_id: str,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize a zone-scoped Carrier entity.

        Args:
            entity_name: Friendly suffix appended to the zone name for display.
            coordinator: Coordinator that supplies Carrier system data.
            system_serial: Carrier system serial used as coordinator context.
            zone_api_id: Carrier API identifier for the represented zone.
            unique_id_suffix: Optional stable suffix appended to the zone unique ID.
        """
        self.zone_api_id = zone_api_id
        self.zone_name = self.resolve_zone_name(coordinator, system_serial, zone_api_id)
        zone_unique_id_suffix = f"zone_{zone_api_id}"
        if unique_id_suffix is not None:
            zone_unique_id_suffix = f"{zone_unique_id_suffix}_{unique_id_suffix}"
        combined_entity_name = self.zone_name
        if entity_name:
            combined_entity_name = f"{self.zone_name} {entity_name}"
        super().__init__(
            entity_name=combined_entity_name,
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=zone_unique_id_suffix,
        )
