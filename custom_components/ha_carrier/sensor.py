from __future__ import annotations
import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import (
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
    PERCENTAGE,
    UnitOfPressure,
)
from homeassistant.config_entries import ConfigEntry
from carrier_api import TemperatureUnits


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
                TemperatureSensor(updater),
                StaticPressureSensor(updater),
                FilterUsedSensor(updater),
            ]
        )
    async_add_entities(entities)


class TemperatureSensor(CarrierEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, updater):
        super().__init__("Outdoor Temperature", updater)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if (
            self._updater.carrier_system.status.temperature_unit
            == TemperatureUnits.FAHRENHEIT
        ):
            return TEMP_FAHRENHEIT
        else:
            return TEMP_CELSIUS

    @property
    def native_value(self) -> float:
        return self._updater.carrier_system.status.outdoor_temperature


class StaticPressureSensor(CarrierEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.INHG

    def __init__(self, updater):
        super().__init__("Static Pressure", updater)

    @property
    def native_value(self) -> float:
        return self._updater.carrier_system.config.static_pressure

    @property
    def available(self) -> bool:
        return self.native_value is not None


class FilterUsedSensor(CarrierEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, updater):
        super().__init__("Filter Remaining", updater)

    @property
    def native_value(self) -> float:
        if self._updater.carrier_system.status.filter_used is not None:
            return 100 - self._updater.carrier_system.status.filter_used

    @property
    def available(self) -> bool:
        return self.native_value is not None
