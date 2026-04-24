"""Expose Carrier telemetry, energy, and status sensors to Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from logging import Logger, getLogger
from typing import Any

from carrier_api import TemperatureUnits
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity
from .const import DATA_UPDATE_COORDINATOR, DOMAIN

_LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create and register Carrier sensor entities for each discovered system.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier integration config entry.
        async_add_entities: Callback used to register created entities.

    Returns:
        None: Entities are registered through the callback.
    """
    updater: CarrierDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        DATA_UPDATE_COORDINATOR
    ]
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
        if carrier_system.profile.outdoor_unit_type in ["varcaphp", "varcapac"]:
            entities.append(OutDoorUnitVarSensor(updater, carrier_system.profile.serial))
        if carrier_system.config.humidifier_enabled:
            entities.append(HumidifierRemainingSensor(updater, carrier_system.profile.serial))
        if carrier_system.config.uv_enabled:
            entities.append(UVLampRemainingSensor(updater, carrier_system.profile.serial))
        for electric_metric in [
            "cooling",
            "hp_heat",
            "fan",
            "electric_heat",
            "reheat",
            "fan_gas",
            "loop_pump",
        ]:
            if getattr(carrier_system.energy, electric_metric):
                entities.append(
                    EnergyMeasurementSensor(updater, carrier_system.profile.serial, electric_metric)
                )
                entities.append(
                    DailyEnergyMeasurementSensor(
                        updater, carrier_system.profile.serial, electric_metric
                    )
                )
                entities.append(
                    MonthlyEnergyMeasurementSensor(
                        updater, carrier_system.profile.serial, electric_metric
                    )
                )
        if carrier_system.energy.gas:
            if updater.system(carrier_system.profile.serial) is not None:
                entities.append(GasMeasurementSensor(updater, carrier_system.profile.serial, "gas"))
            if carrier_system.config.fuel_type == "propane":
                entities.append(PropaneMeasurementSensor(updater, carrier_system.profile.serial))
        for zone in carrier_system.config.zones:
            entities.extend(
                [
                    ZoneTemperatureSensor(
                        updater, carrier_system.profile.serial, zone_api_id=zone.api_id
                    ),
                    ZoneHumiditySensor(
                        updater, carrier_system.profile.serial, zone_api_id=zone.api_id
                    ),
                ]
            )
    async_add_entities(entities)


class ZoneHumiditySensor(CarrierEntity, SensorEntity):
    """Sensor entity that reports current humidity for a specific zone."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str):
        """Initialize a zone humidity sensor.

        Args:
            updater: Coordinator that provides system and zone data.
            system_serial: Carrier system serial tied to the zone.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        super().__init__(f"{self._config_zone.name} Humidity", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return current zone humidity.

        Returns:
            float: Relative humidity percentage for the zone.
        """
        return self._status_zone.humidity

    @property
    def available(self) -> bool:
        """Indicate whether humidity data can be shown.

        Returns:
            bool: True when a zone humidity value is present.
        """
        return self.native_value is not None


class GasMeasurementSensor(CarrierEntity, SensorEntity):
    """Yearly gas usage sensor with fuel-specific unit conversion."""

    def __init__(
        self, updater: CarrierDataUpdateCoordinator, system_serial: str, metric: str
    ) -> None:
        """Initialize a yearly gas consumption sensor.

        Args:
            updater: Coordinator that provides system energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Carrier energy metric key used as the sensor key.

        Raises:
            ValueError: Raised when the system serial cannot be resolved.
        """
        carrier_system = updater.system(system_serial=system_serial)
        if carrier_system is None:
            raise ValueError(
                f"No carrier system found for serial {system_serial!r}; "
                "cannot initialize GasMeasurementSensor."
            )
        self.fuel_type = carrier_system.config.fuel_type
        unit_of_measurement = UnitOfVolume.CUBIC_METERS  # for therms
        if self.fuel_type == "propane":
            unit_of_measurement = UnitOfVolume.CUBIC_FEET
        self.entity_description = SensorEntityDescription(
            key=metric,
            device_class=SensorDeviceClass.GAS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=unit_of_measurement,
            suggested_display_precision=2,
        )
        super().__init__(f"{self.fuel_type.capitalize()} Yearly", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return yearly gas usage converted to the configured gas unit.

        Returns:
            float: Converted yearly gas consumption value.
        """
        value = self.carrier_system.energy.current_year_measurements().gas
        match self.carrier_system.config.gas_unit:
            case "gallon":
                value = (
                    value / 2.54998
                )  # Convert kBTU to cubic feet (1 cubic foot = 2,549.98 BTU, so divide by 2.54998)
            case "therm":
                value = (
                    value / 100 * 2.8328611898017
                )  # /100 to therms then * to convert from therms to cubic meters
            case "gjoule":
                value = value / 100 * 25.5
            # /100 to gjoules (because carrier keeps it an integer in the api
            # response even though it is a float) then * to convert from gjoules
            # to cubic meters
        return value

    @property
    def available(self) -> bool:
        """Indicate whether gas usage data can be displayed.

        Returns:
            bool: True when a computed gas value is available.
        """
        return self.native_value is not None


class PropaneMeasurementSensor(CarrierEntity, SensorEntity):
    """Yearly propane usage sensor expressed in gallons."""

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize a yearly propane consumption sensor.

        Args:
            updater: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
        """
        self.entity_description = SensorEntityDescription(
            key="propane",
            device_class=SensorDeviceClass.VOLUME,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfVolume.GALLONS,
            suggested_display_precision=2,
        )
        super().__init__("Propane Yearly Gallons", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return yearly propane usage converted from kBTU to gallons.

        Returns:
            float: Converted yearly propane volume in gallons.
        """
        value = self.carrier_system.energy.current_year_measurements().gas
        return (
            value / 91.69
        )  # Convert kBTU to gallons (1 gallon LPG = 91,690 BTU, so divide by 91.69)

    @property
    def available(self) -> bool:
        """Indicate whether propane data can be displayed.

        Returns:
            bool: True when a propane value is available.
        """
        return self.native_value is not None


class EnergyMeasurementSensor(CarrierEntity, SensorEntity):
    """Yearly electrical energy consumption sensor for a Carrier metric."""

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, metric: str):
        """Initialize a yearly energy measurement sensor.

        Args:
            updater: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Name of the Carrier energy metric to expose.
        """
        self.entity_description = SensorEntityDescription(
            key=metric,
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=0,
        )
        super().__init__(f"{self.entity_description.key} Energy Yearly", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return yearly energy value for the configured metric.

        Returns:
            float: Yearly kWh total for the metric.
        """
        return getattr(
            self.carrier_system.energy.current_year_measurements(), self.entity_description.key
        )

    @property
    def available(self) -> bool:
        """Indicate whether energy data can be displayed.

        Returns:
            bool: True when a metric value is available.
        """
        return self.native_value is not None


class DailyEnergyMeasurementSensor(CarrierEntity, SensorEntity):
    """Sensor for yesterday's energy usage by Carrier metric."""

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, metric: str):
        """Initialize a daily energy sensor for a specific metric.

        Args:
            updater: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Internal Carrier metric name to expose.
        """
        # Map metric names to API field names
        self.metric_map = {
            "cooling": "coolingKwh",
            "hp_heat": "hPHeatKwh",
            "fan": "fanKwh",
            "electric_heat": "eHeatKwh",
            "reheat": "reheatKwh",
            "fan_gas": "fanGasKwh",
            "loop_pump": "loopPumpKwh",
        }
        self.metric = metric
        self.entity_description = SensorEntityDescription(
            key=f"{metric}_daily",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=1,
        )
        super().__init__(f"{metric} Energy Yesterday", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return yesterday's energy consumption for this metric.

        Returns:
            float: Previous-day consumption in kWh.
        """
        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        for period in energy_periods:
            if period.get("energyPeriodType") == "day1":
                api_field = self.metric_map.get(self.metric)
                return period.get(api_field, 0)
        return 0

    @property
    def available(self) -> bool:
        """Indicate whether raw energy period data is available.

        Returns:
            bool: True when energy payload data is present.
        """
        return self.carrier_system.energy.raw is not None


class MonthlyEnergyMeasurementSensor(CarrierEntity, SensorEntity):
    """Sensor for last month's energy usage by Carrier metric."""

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, metric: str):
        """Initialize a monthly energy sensor for a specific metric.

        Args:
            updater: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Internal Carrier metric name to expose.
        """
        # Map metric names to API field names
        self.metric_map = {
            "cooling": "coolingKwh",
            "hp_heat": "hPHeatKwh",
            "fan": "fanKwh",
            "electric_heat": "eHeatKwh",
            "reheat": "reheatKwh",
            "fan_gas": "fanGasKwh",
            "loop_pump": "loopPumpKwh",
        }
        self.metric = metric
        self.entity_description = SensorEntityDescription(
            key=f"{metric}_monthly",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=0,
        )
        super().__init__(f"{metric} Energy Last Month", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return last month's energy consumption for this metric.

        Returns:
            float: Previous-month consumption in kWh.
        """
        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        for period in energy_periods:
            if period.get("energyPeriodType") == "month1":
                api_field = self.metric_map.get(self.metric)
                return period.get(api_field, 0)
        return 0

    @property
    def available(self) -> bool:
        """Indicate whether raw energy period data is available.

        Returns:
            bool: True when energy payload data is present.
        """
        return self.carrier_system.energy.raw is not None


class ZoneTemperatureSensor(CarrierEntity, SensorEntity):
    """Sensor entity that reports current temperature for a specific zone."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str):
        """Initialize a zone temperature sensor.

        Args:
            updater: Coordinator that provides system and zone data.
            system_serial: Carrier system serial tied to the zone.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        super().__init__(f"{self._config_zone.name} Temperature", updater, system_serial)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the active unit used for zone temperature values.

        Returns:
            str | None: Fahrenheit or Celsius unit constant.
        """
        if self.carrier_system.status.temperature_unit == TemperatureUnits.FAHRENHEIT:
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        """Return current zone temperature.

        Returns:
            float: Temperature reported for the zone.
        """
        return self._status_zone.temperature

    @property
    def available(self) -> bool:
        """Indicate whether temperature data can be shown.

        Returns:
            bool: True when a zone temperature value is present.
        """
        return self.native_value is not None


class OutdoorTemperatureSensor(CarrierEntity, SensorEntity):
    """Sensor entity that reports outdoor ambient temperature."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize an outdoor temperature sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("Outdoor Temperature", updater, system_serial)

    @property
    def native_value(self) -> float:
        """Return current outdoor temperature.

        Returns:
            float: Outdoor temperature reported by Carrier.
        """
        return self.carrier_system.status.outdoor_temperature

    @property
    def available(self) -> bool:
        """Indicate whether outdoor temperature data is available.

        Returns:
            bool: True when an outdoor temperature value exists.
        """
        return self.native_value is not None


class FilterUsedSensor(CarrierEntity, SensorEntity):
    """Filter life sensor represented as a battery-style percentage."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize a filter remaining-life sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("Filter Remaining", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """Return remaining filter life as a percentage.

        Returns:
            float | None: Remaining percentage, or None when unavailable.
        """
        if self.carrier_system.status.filter_used is not None:
            return 100 - self.carrier_system.status.filter_used
        return None

    @property
    def available(self) -> bool:
        """Indicate whether filter life can be displayed.

        Returns:
            bool: True when a filter value is available.
        """
        return self.native_value is not None


class HumidifierRemainingSensor(CarrierEntity, SensorEntity):
    """Humidifier level sensor represented as a remaining percentage."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize a humidifier remaining-life sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("Humidifier Remaining", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """Return remaining humidifier capacity as a percentage.

        Returns:
            float | None: Remaining percentage, or None when unavailable.
        """
        if self.carrier_system.status.humidity_level is not None:
            return 100 - self.carrier_system.status.humidity_level
        return None

    @property
    def available(self) -> bool:
        """Indicate whether humidifier remaining data is available.

        Returns:
            bool: True when a humidifier value is available.
        """
        return self.native_value is not None


class UVLampRemainingSensor(CarrierEntity, SensorEntity):
    """UV lamp life sensor represented as a remaining percentage."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightbulb-fluorescent-tube-outline"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize a UV lamp remaining-life sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("UV Lamp Remaining", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """Return remaining UV lamp life as a percentage.

        Returns:
            float | None: Remaining percentage, or None when unavailable.
        """
        if self.carrier_system.status.uv_lamp_level is not None:
            return 100 - self.carrier_system.status.uv_lamp_level
        return None

    @property
    def available(self) -> bool:
        """Indicate whether UV lamp remaining data is available.

        Returns:
            bool: True when a UV lamp value is available.
        """
        return self.native_value is not None


class TimestampSensor(CarrierEntity, SensorEntity):
    """Timestamp sensor for coordinator refresh and websocket update moments."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, key: str):
        """Initialize a timestamp sensor bound to one coordinator timestamp key.

        Args:
            updater: Coordinator that owns timestamp fields.
            system_serial: Carrier system serial for this entity.
            key: Timestamp suffix such as "all_data", "websocket", or "energy".
        """
        super().__init__(f"updated {key.replace('_', ' ').capitalize()} at", updater, system_serial)
        self.key = key

    @property
    def native_value(self) -> datetime | None:
        """Return the coordinator timestamp for this sensor key.

        Returns:
            datetime | None: Last recorded timestamp for the tracked update path.
        """
        return getattr(self.coordinator, f"timestamp_{self.key}")

    @property
    def available(self) -> bool:
        """Indicate whether a timestamp value exists.

        Returns:
            bool: True when the underlying coordinator timestamp is populated.
        """
        return self.native_value is not None


class AirflowSensor(CarrierEntity, SensorEntity):
    """Sensor entity that reports indoor airflow in CFM."""

    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_FEET_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:fan"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize an airflow sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("Airflow", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """Return airflow in cubic feet per minute.

        Returns:
            float | None: Airflow value as an integer-converted CFM reading.
        """
        if self.carrier_system.status.airflow_cfm is not None:
            return int(self.carrier_system.status.airflow_cfm)
        return None

    @property
    def available(self) -> bool:
        """Indicate whether airflow data can be displayed.

        Returns:
            bool: True when airflow telemetry is available.
        """
        return self.native_value is not None


class StaticPressureSensor(CarrierEntity, SensorEntity):
    """Sensor entity that reports system static pressure."""

    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.INH2O
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize a static pressure sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("Static Pressure", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """Return current static pressure reading.

        Returns:
            float | None: Static pressure value when provided by Carrier.
        """
        if self.carrier_system.status.static_pressure is not None:
            return self.carrier_system.status.static_pressure
        return None

    @property
    def available(self) -> bool:
        """Indicate whether static pressure data is available.

        Returns:
            bool: True when pressure telemetry exists.
        """
        return self.native_value is not None


class OutdoorUnitOperationalStatusSensor(CarrierEntity, SensorEntity):
    """Sensor for outdoor unit operational status and related raw details."""

    _attr_icon = "mdi:hvac"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize an outdoor unit operational status sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("ODU Status", updater, system_serial)
        self.entity_description = SensorEntityDescription(
            key="ODU Status", device_class=SensorDeviceClass.ENUM
        )

    @property
    def native_value(self) -> Any | None:
        """Return normalized outdoor unit operational status.

        Numeric string payloads are mapped to "on" to improve Home Assistant
        logbook phrasing.

        Returns:
            Any | None: Normalized status value or None when unavailable.
        """
        value = self.carrier_system.status.outdoor_unit_operational_status
        if value is not None:
            if isinstance(value, str) and value.isdigit():
                return "on"
            return value
        return None

    @property
    def available(self) -> bool:
        """Indicate whether operational status data is available.

        Returns:
            bool: True when a status value can be shown.
        """
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return raw outdoor unit attributes from the Carrier payload.

        Returns:
            Mapping[str, Any] | None: Raw outdoor-unit subsection from status data.
        """
        return self.carrier_system.status.raw["odu"]


class IndoorUnitOperationalStatusSensor(CarrierEntity, SensorEntity):
    """Sensor for indoor unit operational status and related raw details."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:hvac"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize an indoor unit operational status sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("IDU Status", updater, system_serial)

    @property
    def native_value(self) -> str | None:
        """Return indoor unit operational status.

        Returns:
            str | None: Reported indoor unit status value.
        """
        if self.carrier_system.status.indoor_unit_operational_status is not None:
            return self.carrier_system.status.indoor_unit_operational_status
        return None

    @property
    def available(self) -> bool:
        """Indicate whether operational status data is available.

        Returns:
            bool: True when a status value can be shown.
        """
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return raw indoor unit attributes from the Carrier payload.

        Returns:
            Mapping[str, Any] | None: Raw indoor-unit subsection from status data.
        """
        return self.carrier_system.status.raw["idu"]


class OutDoorUnitVarSensor(CarrierEntity, SensorEntity):
    """Sensor for variable-capacity outdoor unit percentage output."""

    _attr_icon = "mdi:percent-box"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Initialize a variable outdoor unit percentage sensor.

        Args:
            updater: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__("ODU Var", updater, system_serial)

    @property
    def native_value(self) -> float | None:
        """Return normalized variable outdoor unit percentage.

        Returns:
            float | None: Percentage when the payload is numeric, else None.
        """
        value = self.carrier_system.status.outdoor_unit_operational_status
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @property
    def available(self) -> bool:
        """Indicate whether variable-capacity percentage is available.

        Returns:
            bool: True when a normalized percentage value exists.
        """
        return self.native_value is not None
