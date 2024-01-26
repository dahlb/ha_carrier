"""Create binary sensors."""

from __future__ import annotations
from logging import Logger, getLogger

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, DATA_SYSTEMS
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create instances of binary sensors."""
    updaters: list[CarrierDataUpdateCoordinator] = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_SYSTEMS]
    entities = []
    for updater in updaters:
        entities.extend(
            [
                OnlineSensor(updater),
                HumidifierSensor(updater),
            ]
        )
        for zone in updater.carrier_system.config.zones:
            entities.extend(
                [
                    OccupancySensor(updater, zone_api_id=zone.api_id),
                ]
            )
    async_add_entities(entities)


class OnlineSensor(CarrierEntity, BinarySensorEntity):
    """Indicates if thermostat is online."""

    _attr_icon = "mdi:wifi-check"

    def __init__(self, updater):
        """Declare device class and identifiers."""
        self.entity_description = BinarySensorEntityDescription(
            key=f"#{updater.carrier_system.serial}-online",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
        )
        super().__init__("Online", updater)

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return not self._updater.carrier_system.status.is_disconnected

    @property
    def icon(self) -> str | None:
        """Picks icon."""
        if self.is_on:
            return self._attr_icon
        else:
            return "mdi:wifi-strength-outline"


class OccupancySensor(CarrierEntity, BinarySensorEntity):
    """Displays occupancy state."""

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, updater, zone_api_id: str):
        """Create identifiers."""
        self.zone_api_id: str = zone_api_id
        self._updater = updater
        super().__init__(f"{self._status_zone.name} Occupancy", updater)

    @property
    def _status_zone(self):
        for zone in self._updater.carrier_system.status.zones:
            if zone.api_id == self.zone_api_id:
                return zone

    @property
    def is_on(self) -> bool | None:
        """Return true if occupied."""
        return self._status_zone.occupancy


class HumidifierSensor(CarrierEntity, BinarySensorEntity):
    """Displays occupancy state."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, updater):
        """Create identifiers."""
        self._updater = updater
        super().__init__("Humidifier Running", updater)

    @property
    def is_on(self) -> bool | None:
        if self._updater.carrier_system.status.humidifier_on is not None:
            return self._updater.carrier_system.status.humidifier_on
