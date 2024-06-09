"""Create sensors."""

from __future__ import annotations
from logging import Logger, getLogger

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTime,
    UnitOfVolumeFlowRate,
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
                OutdoorTemperatureSensor(updater),
                StaticPressureSensor(updater),
                FilterUsedSensor(updater),
                StatusAgeSensor(updater),
                AirflowSensor(updater),
                OutdoorUnitOperationalStatusSensor(updater),
                IndoorUnitOperationalStatusSensor(updater),
            ]
        )
        for zone in updater.carrier_system.config.zones:
            entities.extend(
                [
                    ZoneTemperatureSensor(updater, zone_api_id=zone.api_id),
                    ZoneHumiditySensor(updater, zone_api_id=zone.api_id),
                ]
            )
    async_add_entities(entities)


class ZoneHumiditySensor(CarrierEntity, SensorEntity):
    """Displays humidity at zone."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, updater, zone_api_id: str):
        """Create identifiers."""
        self.zone_api_id: str = zone_api_id
        self._updater = updater
        super().__init__(f"{self._status_zone.name} Humidity", updater)

    @property
    def _status_zone(self):
        for zone in self._updater.carrier_system.status.zones:
            if zone.api_id == self.zone_api_id:
                return zone

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self._status_zone.humidity


class ZoneTemperatureSensor(CarrierEntity, SensorEntity):
    """Displays temperature at zone."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, updater, zone_api_id: str):
        """Create identifiers."""
        self.zone_api_id: str = zone_api_id
        self._updater = updater
        super().__init__(f"{self._status_zone.name} Temperature", updater)

    @property
    def _status_zone(self):
        for zone in self._updater.carrier_system.status.zones:
            if zone.api_id == self.zone_api_id:
                return zone

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Returns unit of temperature."""
        if (
            self._updater.carrier_system.status.temperature_unit
            == TemperatureUnits.FAHRENHEIT
        ):
            return UnitOfTemperature.FAHRENHEIT
        else:
            return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self._status_zone.temperature


class OutdoorTemperatureSensor(CarrierEntity, SensorEntity):
    """Temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, updater):
        """Temperature sensor."""
        super().__init__("Outdoor Temperature", updater)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Returns unit of temperature."""
        return UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self._updater.carrier_system.status.outdoor_temperature


class StaticPressureSensor(CarrierEntity, SensorEntity):
    """Static Pressure sensor."""

    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.PSI

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
    _attr_icon = "mdi:air-filter"

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


class AirflowSensor(CarrierEntity, SensorEntity):
    """Airflow sensor."""

    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_FEET_PER_MINUTE
    _attr_icon = "mdi:fan"

    def __init__(self, updater):
        """Airflow sensor."""
        super().__init__("Airflow", updater)

    @property
    def native_value(self) -> float:
        """Return airflow in cfm."""
        if self._updater.carrier_system.status.airflow_cfm is not None:
            return int(self._updater.carrier_system.status.airflow_cfm)

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class OutdoorUnitOperationalStatusSensor(CarrierEntity, SensorEntity):
    """Outdoor unit operational status sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:hvac"

    def __init__(self, updater):
        """Creates outdoor unit operational status sensor."""
        super().__init__("ODU Status", updater)

    @property
    def native_value(self) -> float:
        """Return outdoor unit operational status."""
        if self._updater.carrier_system.status.outdoor_unit_operational_status is not None:
            return self._updater.carrier_system.status.outdoor_unit_operational_status

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class IndoorUnitOperationalStatusSensor(CarrierEntity, SensorEntity):
    """Indoor unit operational status sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:hvac"

    def __init__(self, updater):
        """Creates indoor unit operational status sensor."""
        super().__init__("IDU Status", updater)

    @property
    def native_value(self) -> float:
        """Return indoor unit operational status."""
        if self._updater.carrier_system.status.indoor_unit_operational_status is not None:
            return self._updater.carrier_system.status.indoor_unit_operational_status

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None
