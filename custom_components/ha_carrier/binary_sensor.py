"""Expose Carrier binary sensor entities for connectivity and runtime state."""

from __future__ import annotations

import logging

from carrier_api import System
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConfigEntryCarrier
from .coordinator import CarrierDataUpdateCoordinator
from .entity import CarrierEntity, CarrierZoneEntity, async_add_entities_for_systems

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Read-only platform: the coordinator fetches data centrally, so entity updates
# never call Carrier and need no limit.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntryCarrier,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register Carrier binary sensor entities for one config entry.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry that owns the Carrier account.
        async_add_entities: Callback used by Home Assistant to register entities.
    """
    coordinator = config_entry.runtime_data
    async_add_entities_for_systems(
        config_entry,
        async_add_entities,
        lambda carrier_system: _system_entities(coordinator, carrier_system),
    )


def _system_entities(
    coordinator: CarrierDataUpdateCoordinator,
    carrier_system: System,
) -> list[BinarySensorEntity]:
    """Build this platform's entities for one Carrier system.

    Args:
        coordinator: Coordinator that supplies Carrier system data.
        carrier_system: Carrier system to build entities for.

    Returns:
        list[BinarySensorEntity]: Entities for the system.
    """
    entities: list[BinarySensorEntity] = []
    entities.append(
        OnlineSensor(coordinator=coordinator, system_serial=carrier_system.profile.serial)
    )
    if carrier_system.config.humidifier_enabled:
        entities.append(
            HumidifierSensor(coordinator=coordinator, system_serial=carrier_system.profile.serial)
        )
    entities.extend(
        OccupancySensor(
            coordinator=coordinator,
            system_serial=carrier_system.profile.serial,
            zone_api_id=zone.api_id,
        )
        for zone in carrier_system.config.zones
        if zone.occupancy_enabled
    )
    if carrier_system.profile.indoor_unit_type not in (None, "", "none"):
        entities.append(
            IndoorUnitLockoutSensor(
                coordinator=coordinator, system_serial=carrier_system.profile.serial
            )
        )
    if carrier_system.profile.outdoor_unit_type not in (None, "", "none"):
        entities.append(
            OutdoorUnitLockoutSensor(
                coordinator=coordinator, system_serial=carrier_system.profile.serial
            )
        )
    return entities


class CarrierBinarySensor(CarrierEntity, BinarySensorEntity):
    """Shared Carrier base class for system-level binary sensor entities."""

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str | None = None,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize a Carrier binary sensor entity.

        Args:
            entity_name: Friendly suffix used in entity name and unique ID.
            coordinator: Coordinator that provides Carrier data.
            system_serial: Carrier system serial for this entity.
            unique_id_suffix: Optional stable suffix used for the entity unique ID.

        Raises:
            ValueError: Raised when no Carrier system serial is provided.
        """
        if system_serial is None:
            raise ValueError("Carrier binary sensor system serial is required")
        super().__init__(
            entity_name=entity_name,
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=unique_id_suffix,
        )
        self._sync_entity_attrs()

    def _update_entity_attrs(self) -> None:
        """Default to unavailable so concrete sensors must opt in to data.

        Each concrete binary sensor subclass overrides this to set
        ``_attr_is_on`` and to flip ``_attr_available`` to True once it has
        successfully read its value from the coordinator.
        """
        self._attr_available = False


class CarrierZoneBinarySensor(CarrierZoneEntity, CarrierBinarySensor):
    """Shared Carrier base class for zone-backed binary sensor entities."""

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        zone_api_id: str,
        unique_id_suffix: str,
    ) -> None:
        """Initialize a zone-backed Carrier binary sensor entity.

        Args:
            entity_name: Friendly suffix appended to the zone name for display.
            coordinator: Coordinator that provides Carrier data.
            system_serial: Carrier system serial for this entity.
            zone_api_id: Carrier API identifier for the represented zone.
            unique_id_suffix: Stable suffix used in the zone entity unique ID.
        """
        super().__init__(
            entity_name,
            coordinator,
            system_serial,
            zone_api_id,
            unique_id_suffix=unique_id_suffix,
        )


class OnlineSensor(CarrierBinarySensor):
    """Binary sensor that reports whether the Carrier system is reachable."""

    _attr_translation_key = "online"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize connectivity metadata for a Carrier system.

        Args:
            coordinator: Coordinator that provides system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__("Online", coordinator, system_serial)

    def _update_entity_attrs(self) -> None:
        """Update connectivity attrs from coordinator data."""
        self._attr_is_on = not self.carrier_system.status.is_disconnected
        self._attr_available = True


class OccupancySensor(CarrierZoneBinarySensor):
    """Binary sensor that mirrors occupancy detection for a Carrier zone."""

    _attr_translation_key = "occupancy"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str
    ) -> None:
        """Initialize an occupancy entity tied to one zone.

        Args:
            coordinator: Coordinator that provides system and zone state.
            system_serial: Unique Carrier system serial number.
            zone_api_id: API identifier for the target zone.
        """
        super().__init__(
            entity_name="Occupancy",
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix="occupancy",
        )

    def _update_entity_attrs(self) -> None:
        """Update occupancy attrs from coordinator data."""
        self._attr_is_on = self._status_zone.occupancy
        self._attr_available = self._attr_is_on is not None


class HumidifierSensor(CarrierBinarySensor):
    """Binary sensor that indicates whether humidification is active."""

    _attr_translation_key = "humidifier_running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a humidifier runtime sensor for one Carrier system.

        Args:
            coordinator: Coordinator that provides system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__("Humidifier Running", coordinator, system_serial)

    def _update_entity_attrs(self) -> None:
        """Update humidifier attrs from coordinator data."""
        self._attr_is_on = self.carrier_system.status.humidifier_on
        self._attr_available = self._attr_is_on is not None


class OutdoorUnitLockoutSensor(CarrierBinarySensor):
    """Problem sensor that is on while the outdoor unit is locked out.

    Carrier reports lockouts only in diagnostic-device-info websocket messages,
    so the sensor stays unavailable on systems that never send those and is
    disabled by default.
    """

    _attr_translation_key = "outdoor_unit_lockout"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize an outdoor unit lockout sensor for one Carrier system.

        Args:
            coordinator: Coordinator that provides system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__(
            "Outdoor Unit Lockout",
            coordinator,
            system_serial,
            unique_id_suffix="outdoor_unit_lockout",
        )

    def _update_entity_attrs(self) -> None:
        """Update lockout attrs from coordinator data."""
        outdoor_unit = self.carrier_system.status.outdoor_unit
        self._attr_is_on = None if outdoor_unit is None else outdoor_unit.lockout_active
        self._attr_available = self._attr_is_on is not None


class IndoorUnitLockoutSensor(CarrierBinarySensor):
    """Problem sensor that is on while the indoor unit (furnace or fan coil) is locked out.

    Carrier reports the flag as ``lockoutactive`` in the indoor unit's status, so
    the sensor is unavailable while that is missing. Disabled by default, like
    the outdoor unit lockout sensor.
    """

    _attr_translation_key = "indoor_unit_lockout"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize an indoor unit lockout sensor for one Carrier system.

        Args:
            coordinator: Coordinator that provides system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__(
            "Indoor Unit Lockout",
            coordinator,
            system_serial,
            unique_id_suffix="indoor_unit_lockout",
        )

    def _update_entity_attrs(self) -> None:
        """Update lockout attrs from coordinator data."""
        indoor_unit = self.carrier_system.status.indoor_unit
        self._attr_is_on = None if indoor_unit is None else indoor_unit.lockout_active
        self._attr_available = self._attr_is_on is not None
