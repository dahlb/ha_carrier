"""Create sensors."""

from __future__ import annotations
from logging import Logger, getLogger

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import (
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTime,
)
from homeassistant.config_entries import ConfigEntry
from datetime import datetime
from carrier_api import TemperatureUnits


from .const import DOMAIN, DATA_SYSTEMS
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create sensors."""
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
                StatusAgeSensor(updater),
            ]
        )
    async_add_entities(entities)


class TemperatureSensor(CarrierEntity, SensorEntity):
    """Temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, updater):
        """Temperature sensor."""
        super().__init__("Outdoor Temperature", updater)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Returns unit of temperature."""
        if (
            self._updater.carrier_system.status.temperature_unit
            == TemperatureUnits.FAHRENHEIT
        ):
            return TEMP_FAHRENHEIT
        else:
            return TEMP_CELSIUS

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self._updater.carrier_system.status.outdoor_temperature


class StaticPressureSensor(CarrierEntity, SensorEntity):
    """Static Pressure sensor."""

    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.INHG

    def __init__(self, updater):
        """Create static pressure sensor."""
        super().__init__("Static Pressure", updater)

    @property
    def native_value(self) -> float:
        """Returns static pressure value."""
        return self._updater.carrier_system.config.static_pressure

    @property
    def available(self) -> bool:
        """Returns if sensor is ready to be displayed."""
        return self.native_value is not None


class FilterUsedSensor(CarrierEntity, SensorEntity):
    """Filter used sensor, mimics battery for easy testing."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, updater):
        """Filter used sensor."""
        super().__init__("Filter Remaining", updater)

    @property
    def native_value(self) -> float:
        """Return percentage remaining."""
        if self._updater.carrier_system.status.filter_used is not None:
            return 100 - self._updater.carrier_system.status.filter_used

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class StatusAgeSensor(CarrierEntity, SensorEntity):
    """Time since thermostat updated the api last."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(self, updater):
        """Time since thermostat updated the api last."""
        super().__init__("Status Minutes Since Updating", updater)

    @property
    def native_value(self) -> float:
        """Return minutes since thermostat last updated the api."""
        if self._updater.carrier_system.status.time_stamp is not None:
            age_of_last_sync = (
                datetime.now().astimezone()
                - self._updater.carrier_system.status.time_stamp
            )
            return int(age_of_last_sync.total_seconds() / 60)

            return 100 - self._updater.carrier_system.status.filter_used

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None
