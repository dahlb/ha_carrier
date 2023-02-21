from __future__ import annotations
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, DATA_SYSTEMS
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    updaters: list[CarrierDataUpdateCoordinator] = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_SYSTEMS]
    entities = []
    for updater in updaters:
        entities.extend(
            [
                OnlineSensor(updater),
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
    _attr_icon = "mdi:wifi-check"

    def __init__(self, updater):
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
        if self.is_on:
            return self._attr_icon
        else:
            return "mdi:wifi-strength-outline"


class OccupancySensor(CarrierEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, updater, zone_api_id: str):
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
        return self._status_zone.occupancy
