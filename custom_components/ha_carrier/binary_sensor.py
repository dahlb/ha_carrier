"""Create binary sensors."""

from __future__ import annotations
from logging import Logger, getLogger

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, DATA_UPDATE_COORDINATOR
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

_LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create instances of binary sensors."""
    updater: CarrierDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_UPDATE_COORDINATOR]
    entities = []
    for carrier_system in updater.systems:
        entities.extend(
            [
                OnlineSensor(updater, carrier_system.profile.serial),
                HumidifierSensor(updater, carrier_system.profile.serial),
            ]
        )
        for zone in carrier_system.config.zones:
            entities.extend(
                [
                    OccupancySensor(updater, carrier_system.profile.serial, zone_api_id=zone.api_id),
                ]
            )
    async_add_entities(entities)


class OnlineSensor(CarrierEntity, BinarySensorEntity):
    """Indicates if thermostat is online."""
    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Declare device class and identifiers."""
        super().__init__("Online", updater, system_serial)
        self.entity_description = BinarySensorEntityDescription(
            key=f"#{self.carrier_system.profile.serial}-online",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            icon="mdi:wifi-check",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return not self.carrier_system.status.is_disconnected

    @property
    def icon(self) -> str | None:
        """Picks icon."""
        if self.is_on:
            return self.entity_description.icon
        else:
            return "mdi:wifi-strength-outline"

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.is_on is not None


class OccupancySensor(CarrierEntity, BinarySensorEntity):
    """Displays occupancy state."""
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str):
        """Create identifiers."""
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        super().__init__(f"{self._config_zone.name} Occupancy", updater, system_serial)

    @property
    def is_on(self) -> bool | None:
        """Return true if occupied."""
        return self._status_zone.occupancy

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.is_on is not None


class HumidifierSensor(CarrierEntity, BinarySensorEntity):
    """Displays occupancy state."""
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        super().__init__("Humidifier Running", updater, system_serial)

    @property
    def is_on(self) -> bool | None:
        if self.carrier_system.status.humidifier_on is not None:
            return self.carrier_system.status.humidifier_on

    @property
    def icon(self) -> str | None:
        """Picks icon."""
        if self.is_on:
            return "mdi:air-humidifier"
        else:
            return "mdi:air-humidifier-off"

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.is_on is not None
