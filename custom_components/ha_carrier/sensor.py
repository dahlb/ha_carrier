"""Create sensors."""

from __future__ import annotations
from logging import Logger, getLogger
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorEntityDescription, SensorStateClass
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
    UnitOfVolumeFlowRate,
    UnitOfEnergy,
    UnitOfVolume,
)
from homeassistant.config_entries import ConfigEntry
from datetime import datetime
from carrier_api import TemperatureUnits


from .const import DOMAIN, DATA_UPDATE_COORDINATOR
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

_LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create sensors."""
    updater: CarrierDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_UPDATE_COORDINATOR]
    entities = []
    for carrier_system in updater.systems:
        entities.extend(
            [
                OutdoorTemperatureSensor(updater, carrier_system.profile.serial),
                FilterUsedSensor(updater, carrier_system.profile.serial),
                TimestampSensor(updater, carrier_system.profile.serial, "all_data"),
                TimestampSensor(updater, carrier_system.profile.serial, "websocket"),
                TimestampSensor(updater, carrier_system.profile.serial, "energy"),
                AirflowSensor(updater, carrier_system.profile.serial),
                StaticPressureSensor(updater, carrier_system.profile.serial),
                OutdoorUnitOperationalStatusSensor(updater, carrier_system.profile.serial),
                IndoorUnitOperationalStatusSensor(updater, carrier_system.profile.serial),
            ]
        )
        if carrier_system.profile.outdoor_unit_type == "varcaphp":
            entities.append(HPVarSensor(updater, carrier_system.profile.serial))
        if carrier_system.config.humidifier_enabled:
            entities.append(HumidifierRemainingSensor(updater, carrier_system.profile.serial))
        if carrier_system.config.uv_enabled:
            entities.append(UVLampRemainingSensor(updater, carrier_system.profile.serial))
        for electric_metric in ["cooling", "hp_heat", "fan", "electric_heat", "reheat", "fan_gas", "loop_pump"]:
            if getattr(carrier_system.energy, electric_metric):
                entities.append(EnergyMeasurementSensor(updater, carrier_system.profile.serial, electric_metric))
        if carrier_system.energy.gas:
            entities.append(GasMeasurementSensor(updater, carrier_system.profile.serial, "gas"))
            if carrier_system.config.fuel_type == 'propane':
                entities.append(PropaneMeasurementSensor(updater, carrier_system.profile.serial))
        for zone in carrier_system.config.zones:
            entities.extend(
                [
                    ZoneTemperatureSensor(updater, carrier_system.profile.serial, zone_api_id=zone.api_id),
                    ZoneHumiditySensor(updater, carrier_system.profile.serial, zone_api_id=zone.api_id),
                ]
            )
    async_add_entities(entities)


class ZoneHumiditySensor(CarrierEntity, SensorEntity):
    """Displays humidity at zone."""
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str):
        """Create identifiers."""
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        super().__init__(f"{self._config_zone.name} Humidity", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self._status_zone.humidity

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None

class GasMeasurementSensor(CarrierEntity, SensorEntity):
    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, metric: str):
        self.fuel_type = updater.system(system_serial=system_serial).config.fuel_type
        unit_of_measurement = UnitOfVolume.CUBIC_METERS # for therms
        if self.fuel_type == "propane":
            unit_of_measurement = UnitOfVolume.CUBIC_FEET
        self.entity_description = SensorEntityDescription(
            key=metric,
            device_class=SensorDeviceClass.GAS,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=unit_of_measurement,
            suggested_display_precision=2,
            last_reset=datetime(year=datetime.now().year, month=1, day=1)
        )
        super().__init__(f"{self.fuel_type.capitalize()} Yearly", updater, system_serial)

    @property
    def native_value(self) -> float:
        value = self.carrier_system.energy.current_year_measurements().gas
        match self.carrier_system.config.gas_unit:
            case "gallon":
                value = value / 2.54998 # Convert kBTU to cubic feet (1 cubic foot = 2,549.98 BTU, so divide by 2.54998)
            case "therm":
                value = value / 100 * 2.8328611898017 # /100 to therms then * to convert from therms to cubic meters
            case "gjoule":
                value = value / 100 * 25.5  # /100 to gjoules (because carrier keeps it an integer in the api response even though it is a float) then * to convert from gjoules to cubic meters
        return value

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None

class PropaneMeasurementSensor(CarrierEntity, SensorEntity):
    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        self.entity_description = SensorEntityDescription(
            key="propane",
            device_class=SensorDeviceClass.VOLUME,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=UnitOfVolume.GALLONS,
            suggested_display_precision=2,
            last_reset=datetime(year=datetime.now().year, month=1, day=1)
        )
        super().__init__("Propane Yearly Gallons", updater, system_serial)

    @property
    def native_value(self) -> float:
        value = self.carrier_system.energy.current_year_measurements().gas
        return value / 91.69 # Convert kBTU to gallons (1 gallon LPG = 91,690 BTU, so divide by 91.69)

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None

class EnergyMeasurementSensor(CarrierEntity, SensorEntity):
    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, metric: str):
        self.entity_description = SensorEntityDescription(
            key=metric,
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=0,
            last_reset=datetime(year=datetime.now().year, month=1, day=1)
        )
        super().__init__(f"{self.entity_description.key} Energy Yearly", updater, system_serial)

    @property
    def native_value(self) -> float:
        return getattr(self.carrier_system.energy.current_year_measurements(), self.entity_description.key)

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class ZoneTemperatureSensor(CarrierEntity, SensorEntity):
    """Displays temperature at zone."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str):
        """Create identifiers."""
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        super().__init__(f"{self._config_zone.name} Temperature", updater, system_serial)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Returns unit of temperature."""
        if (
            self.carrier_system.status.temperature_unit
            == TemperatureUnits.FAHRENHEIT
        ):
            return UnitOfTemperature.FAHRENHEIT
        else:
            return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self._status_zone.temperature

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class OutdoorTemperatureSensor(CarrierEntity, SensorEntity):
    """Temperature sensor."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Temperature sensor."""
        super().__init__("Outdoor Temperature", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Returns temperature."""
        return self.carrier_system.status.outdoor_temperature

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class FilterUsedSensor(CarrierEntity, SensorEntity):
    """Filter used sensor, mimics battery for easy testing."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Filter used sensor."""
        super().__init__("Filter Remaining", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return percentage remaining."""
        if self.carrier_system.status.filter_used is not None:
            return 100 - self.carrier_system.status.filter_used

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class HumidifierRemainingSensor(CarrierEntity, SensorEntity):
    """Humidifier remaining sensor, mimics battery for easy testing."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Humidifier used sensor."""
        super().__init__("Humidifier Remaining", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return percentage remaining."""
        if self.carrier_system.status.humidity_level is not None:
            return 100 - self.carrier_system.status.humidity_level

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class UVLampRemainingSensor(CarrierEntity, SensorEntity):
    """UV Lamp remaining sensor, mimics battery for easy testing."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightbulb-fluorescent-tube-outline"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """UV Lamp used sensor."""
        super().__init__("UV Lamp Remaining", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return percentage remaining."""
        if self.carrier_system.status.uv_lamp_level is not None:
            return 100 - self.carrier_system.status.uv_lamp_level

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class TimestampSensor(CarrierEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, key: str):
        super().__init__(f"updated {key.replace('_', ' ').capitalize()} at", updater, system_serial)
        self.key = key

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator, f"timestamp_{self.key}")

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class AirflowSensor(CarrierEntity, SensorEntity):
    """Airflow sensor."""
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_FEET_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:fan"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Airflow sensor."""
        super().__init__("Airflow", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return airflow in cfm."""
        if self.carrier_system.status.airflow_cfm is not None:
            return int(self.carrier_system.status.airflow_cfm)

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class StaticPressureSensor(CarrierEntity, SensorEntity):
    """Static Pressure sensor."""
    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = "psi"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Static Pressure sensor."""
        super().__init__("Static Pressure", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return Static Pressure in psi."""
        if self.carrier_system.status.static_pressure is not None:
           return self.carrier_system.status.static_pressure * 0.03613 # convert from inwc to psi

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class OutdoorUnitOperationalStatusSensor(CarrierEntity, SensorEntity):
    """Outdoor unit operational status sensor.
    Maps numeric string values to 'on' for improved logbook phrasing in Home Assistant.
    """
    _attr_icon = "mdi:hvac"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Create outdoor unit operational status sensor."""
        super().__init__("ODU Status", updater, system_serial)
        self.entity_description = SensorEntityDescription(key="ODU Status", device_class=SensorDeviceClass.ENUM)

    @property
    def native_value(self) -> Any | None:
        """
        Return outdoor unit operational status. Numeric strings (e.g., '1') are mapped to 'on'.
        This improves logbook phrasing in Home Assistant.
        """
        value = self.carrier_system.status.outdoor_unit_operational_status
        if value is not None:
            if isinstance(value, str) and value.isdigit():
                return "on"
            return value

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class IndoorUnitOperationalStatusSensor(CarrierEntity, SensorEntity):
    """Indoor unit operational status sensor."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:hvac"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Creates indoor unit operational status sensor."""
        super().__init__("IDU Status", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return indoor unit operational status."""
        if self.carrier_system.status.indoor_unit_operational_status is not None:
            return self.carrier_system.status.indoor_unit_operational_status

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None


class HPVarSensor(CarrierEntity, SensorEntity):
    """HP Var sensor for variable capacity heat pump percentage.
    Only registered for systems with outdoor_unit_type == 'varcaphp'.
    Returns the percentage as a float if the value is a numeric string (including decimals),
    or 0 for any non-numeric value.
    """
    _attr_icon = "mdi:percent-box"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Create HP Var sensor."""
        super().__init__("HP Var", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """
        Return HP Var percentage as a float if the value is a numeric string (including decimals).
        Returns 0 for any non-numeric value.
        """
        value = self.carrier_system.status.outdoor_unit_operational_status
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        return 0

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.native_value is not None
