"""Expose Carrier telemetry, energy, and status sensors to Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
import logging

from carrier_api import TemperatureUnits
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConfigEntryCarrier
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity, CarrierZoneEntity
from .util import TIMESTAMP_TYPES

_LOGGER: logging.Logger = logging.getLogger(__name__)

ENERGY_METRIC_MAP: dict[str, str] = {
    "cooling": "coolingKwh",
    "hp_heat": "hPHeatKwh",
    "fan": "fanKwh",
    "electric_heat": "eHeatKwh",
    "reheat": "reheatKwh",
    "fan_gas": "fanGasKwh",
    "loop_pump": "loopPumpKwh",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntryCarrier,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register Carrier sensor entities for each discovered system.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier integration config entry.
        async_add_entities: Callback used to register created entities.
    """
    coordinator = config_entry.runtime_data
    entities: list[SensorEntity] = []
    for carrier_system in coordinator.systems:
        entities.extend(
            [
                OutdoorTemperatureSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                ),
                FilterUsedSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                ),
                AirflowSensor(coordinator=coordinator, system_serial=carrier_system.profile.serial),
                StaticPressureSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                ),
                OutdoorUnitOperationalStatusSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                ),
                IndoorUnitOperationalStatusSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                ),
            ]
        )
        entities.extend(
            [
                TimestampSensor(
                    coordinator=coordinator,
                    system_serial=carrier_system.profile.serial,
                    timestamp_type=timestamp_type,
                )
                for timestamp_type in TIMESTAMP_TYPES
            ]
        )

        if carrier_system.profile.outdoor_unit_type in ["varcaphp", "varcapac"]:
            entities.append(
                OutdoorUnitVarSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                )
            )
        if carrier_system.config.humidifier_enabled:
            entities.append(
                HumidifierRemainingSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                )
            )
        if carrier_system.config.uv_enabled:
            entities.append(
                UVLampRemainingSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                )
            )
        for electric_metric in ENERGY_METRIC_MAP:
            if getattr(carrier_system.energy, electric_metric, False) is True:
                entities.extend(
                    [
                        YearlyEnergyMeasurementSensor(
                            coordinator=coordinator,
                            system_serial=carrier_system.profile.serial,
                            metric=electric_metric,
                        ),
                        DailyEnergyMeasurementSensor(
                            coordinator=coordinator,
                            system_serial=carrier_system.profile.serial,
                            metric=electric_metric,
                        ),
                        MonthlyEnergyMeasurementSensor(
                            coordinator=coordinator,
                            system_serial=carrier_system.profile.serial,
                            metric=electric_metric,
                        ),
                    ]
                )
        gas_measurement = getattr(carrier_system.energy, "gas", False)
        if gas_measurement is True:
            entities.append(
                GasMeasurementSensor(
                    coordinator=coordinator,
                    system_serial=carrier_system.profile.serial,
                    fuel_type=carrier_system.config.fuel_type,
                )
            )
            if carrier_system.config.fuel_type == "propane":
                entities.append(
                    PropaneMeasurementSensor(
                        coordinator=coordinator, system_serial=carrier_system.profile.serial
                    )
                )
        for zone in carrier_system.config.zones:
            entities.extend(
                [
                    ZoneTemperatureSensor(
                        coordinator=coordinator,
                        system_serial=carrier_system.profile.serial,
                        zone_api_id=zone.api_id,
                    ),
                    ZoneHumiditySensor(
                        coordinator=coordinator,
                        system_serial=carrier_system.profile.serial,
                        zone_api_id=zone.api_id,
                    ),
                ]
            )
    async_add_entities(entities)


class CarrierSensor(CarrierEntity, SensorEntity):
    """Shared Carrier base class for system-level sensor entities."""

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str | None = None,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize a Carrier sensor entity.

        Args:
            entity_name: Friendly suffix used in entity name and unique ID.
            coordinator: Coordinator that provides Carrier data.
            system_serial: Carrier system serial for this entity.
            unique_id_suffix: Optional stable suffix used for the entity unique ID.

        Raises:
            ValueError: Raised when no Carrier system serial is provided.
        """
        if system_serial is None:
            raise ValueError("Carrier sensor system serial is required")
        super().__init__(
            entity_name=entity_name,
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=unique_id_suffix,
        )
        self._sync_entity_attrs()

    def _update_entity_attrs(self) -> None:
        """Update sensor attrs from coordinator data."""
        self._attr_available = False


class CarrierZoneSensor(CarrierZoneEntity, CarrierSensor):
    """Shared Carrier base class for zone-backed sensor entities."""

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        zone_api_id: str,
        unique_id_suffix: str,
    ) -> None:
        """Initialize a zone-backed Carrier sensor entity.

        Args:
            entity_name: Friendly suffix appended to the zone name for display.
            coordinator: Coordinator that provides Carrier data.
            system_serial: Carrier system serial for this entity.
            zone_api_id: Carrier API identifier for the represented zone.
            unique_id_suffix: Stable suffix used in the zone entity unique ID.
        """
        super().__init__(
            entity_name=entity_name,
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix=unique_id_suffix,
        )


class ZoneHumiditySensor(CarrierZoneSensor):
    """Sensor entity that reports current humidity for a specific zone."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str
    ) -> None:
        """Initialize a zone humidity sensor.

        Args:
            coordinator: Coordinator that provides system and zone data.
            system_serial: Carrier system serial tied to the zone.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        super().__init__(
            entity_name="Humidity",
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix="humidity",
        )

    def _update_entity_attrs(self) -> None:
        """Update humidity attrs from coordinator data."""
        self._attr_native_value = self._status_zone.humidity
        self._attr_available = self._attr_native_value is not None


class GasMeasurementSensor(CarrierSensor):
    """Yearly gas usage sensor with fuel-specific unit conversion."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        fuel_type: str,
    ) -> None:
        """Initialize a yearly gas consumption sensor.

        Args:
            coordinator: Coordinator that provides system energy payloads.
            system_serial: Carrier system serial for this entity.
            fuel_type: Configured fuel type for the Carrier system.

        Raises:
            ValueError: Raised when the system serial cannot be resolved.
        """
        self.fuel_type = fuel_type
        super().__init__(
            entity_name=f"{self.fuel_type.capitalize()} Usage Year to Date",
            coordinator=coordinator,
            system_serial=system_serial,
        )

        match self.carrier_system.config.gas_unit:
            case "gallon":
                self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_FEET
                self._attr_suggested_display_precision = 2
            case "therm" | "gjoule":
                self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
                self._attr_suggested_display_precision = 2
            case _:
                self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
                self._attr_suggested_display_precision = 2

    def _update_entity_attrs(self) -> None:
        """Update gas usage attrs from coordinator data."""
        if self.carrier_system.energy.raw is None:
            self._attr_available = False
            return

        api_field = "gas"
        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        value: float | None = None
        for period in energy_periods:
            if period.get("energyPeriodType") == "year1":
                value = period.get(api_field)
                break
        if value is None:
            self._attr_available = False
            return

        match self.carrier_system.config.gas_unit:
            case "gallon":
                # Convert kBTU to cubic feet (1 cubic foot = 2,549.98 BTU, so divide by 2.54998)
                value = value / 2.54998
            case "therm":
                # /100 to therms then * to convert from therms to cubic meters
                value = value / 100 * 2.8328611898017
            case "gjoule":
                # /100 to gjoules (because carrier keeps it an integer in the api
                # response even though it is a float) then * to convert from gjoules
                # to cubic meters
                value = value / 100 * 25.5
            case _:
                self._attr_available = False
                return
        self._attr_native_value = value
        self._attr_available = True


class PropaneMeasurementSensor(CarrierSensor):
    """Yearly propane usage sensor expressed in gallons."""

    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a yearly propane consumption sensor.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="Propane Consumption Year to Date",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update propane usage attrs from coordinator data."""
        if self.carrier_system.energy.raw is None:
            self._attr_available = False
            return

        api_field = "gas"
        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        value: float | None = None
        for period in energy_periods:
            if period.get("energyPeriodType") == "year1":
                value = period.get(api_field)
                break
        if value is None:
            self._attr_available = False
            return

        # Convert kBTU to gallons (1 gallon LPG = 91,690 BTU, so divide by 91.69)
        self._attr_native_value = value / 91.69
        self._attr_available = True


class YearlyEnergyMeasurementSensor(CarrierSensor):
    """Yearly electrical energy consumption sensor for a Carrier metric."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, metric: str
    ) -> None:
        """Initialize a yearly energy measurement sensor.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Name of the Carrier energy metric to expose.
        """
        self.metric = metric
        super().__init__(
            entity_name=f"{metric.replace('_', ' ').title()} Energy Year to Date",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update yearly energy attrs from coordinator data."""
        if self.carrier_system.energy.raw is None:
            self._attr_available = False
            return

        api_field = ENERGY_METRIC_MAP.get(self.metric)
        if api_field is None:
            _LOGGER.debug("Unknown yearly energy metric requested: %s", self.metric)
            self._attr_available = False
            return

        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        for period in energy_periods:
            if period.get("energyPeriodType") == "year1":
                value = period.get(api_field)
                if value is not None:
                    self._attr_native_value = value
                    self._attr_available = True
                    return
        self._attr_available = False


class DailyEnergyMeasurementSensor(CarrierSensor):
    """Sensor for yesterday's energy usage by Carrier metric."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, metric: str
    ) -> None:
        """Initialize a daily energy sensor for a specific metric.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Internal Carrier metric name to expose.
        """
        self.metric = metric
        super().__init__(
            entity_name=f"{metric.replace('_', ' ').title()} Energy Yesterday",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update daily energy attrs from coordinator data."""
        if self.carrier_system.energy.raw is None:
            self._attr_available = False
            return

        api_field = ENERGY_METRIC_MAP.get(self.metric)
        if api_field is None:
            _LOGGER.debug("Unknown daily energy metric requested: %s", self.metric)
            self._attr_available = False
            return

        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        for period in energy_periods:
            if period.get("energyPeriodType") == "day1":
                value = period.get(api_field)
                if value is not None:
                    self._attr_native_value = value
                    self._attr_available = True
                    return
        self._attr_available = False


class MonthlyEnergyMeasurementSensor(CarrierSensor):
    """Sensor for last month's energy usage by Carrier metric."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, metric: str
    ) -> None:
        """Initialize a monthly energy sensor for a specific metric.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Internal Carrier metric name to expose.
        """
        self.metric = metric
        super().__init__(
            entity_name=f"{metric.replace('_', ' ').title()} Energy Last Month",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update monthly energy attrs from coordinator data."""
        if self.carrier_system.energy.raw is None:
            self._attr_available = False
            return

        api_field = ENERGY_METRIC_MAP.get(self.metric)
        if api_field is None:
            _LOGGER.debug("Unknown monthly energy metric requested: %s", self.metric)
            self._attr_available = False
            return

        energy_periods = self.carrier_system.energy.raw.get("energyPeriods", [])
        for period in energy_periods:
            if period.get("energyPeriodType") == "month1":
                value = period.get(api_field)
                if value is not None:
                    self._attr_native_value = value
                    self._attr_available = True
                    return
        self._attr_available = False


class ZoneTemperatureSensor(CarrierZoneSensor):
    """Sensor entity that reports current temperature for a specific zone."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str
    ) -> None:
        """Initialize a zone temperature sensor.

        Args:
            coordinator: Coordinator that provides system and zone data.
            system_serial: Carrier system serial tied to the zone.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        super().__init__(
            entity_name="Temperature",
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix="temperature",
        )

    def _update_entity_attrs(self) -> None:
        """Update temperature attrs from coordinator data."""
        if self.carrier_system.status.temperature_unit == TemperatureUnits.FAHRENHEIT:
            self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
        else:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_native_value = self._status_zone.temperature
        self._attr_available = self._attr_native_value is not None


class OutdoorTemperatureSensor(CarrierSensor):
    """Sensor entity that reports outdoor ambient temperature."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize an outdoor temperature sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="Outdoor Temperature",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update outdoor temperature attrs from coordinator data."""
        if self.carrier_system.status.temperature_unit == TemperatureUnits.FAHRENHEIT:
            self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
        else:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_native_value = self.carrier_system.status.outdoor_temperature
        self._attr_available = self._attr_native_value is not None


class FilterUsedSensor(CarrierSensor):
    """Filter life sensor represented as a percentage."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a filter remaining-life sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="Filter Remaining",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update filter life attrs from coordinator data."""
        if self.carrier_system.status.filter_used is None:
            self._attr_available = False
            return
        self._attr_native_value = 100 - self.carrier_system.status.filter_used
        self._attr_available = True


class HumidifierRemainingSensor(CarrierSensor):
    """Humidifier level sensor represented as a remaining percentage."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a humidifier remaining-life sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="Humidifier Remaining",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update humidifier remaining attrs from coordinator data."""
        if self.carrier_system.status.humidity_level is None:
            self._attr_available = False
            return
        self._attr_native_value = 100 - self.carrier_system.status.humidity_level
        self._attr_available = True


class UVLampRemainingSensor(CarrierSensor):
    """UV lamp life sensor represented as a remaining percentage."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightbulb-fluorescent-tube-outline"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a UV lamp remaining-life sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="UV Lamp Remaining",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update UV lamp remaining attrs from coordinator data."""
        if self.carrier_system.status.uv_lamp_level is None:
            self._attr_available = False
            return
        self._attr_native_value = 100 - self.carrier_system.status.uv_lamp_level
        self._attr_available = True


class TimestampSensor(CarrierSensor):
    """Timestamp sensor for coordinator refresh and websocket update moments."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, timestamp_type: str
    ) -> None:
        """Initialize a timestamp sensor bound to one coordinator timestamp type.

        Args:
            coordinator: Coordinator that owns timestamp fields.
            system_serial: Carrier system serial for this entity.
            timestamp_type: Timestamp suffix such as "all_data", "websocket", or "energy".
        """
        self.timestamp_type = timestamp_type
        super().__init__(
            entity_name=f"{timestamp_type.replace('_', ' ').title()} Last Updated",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update timestamp attrs from coordinator data."""
        self._attr_native_value = getattr(self.coordinator, f"timestamp_{self.timestamp_type}")
        self._attr_available = self._attr_native_value is not None


class AirflowSensor(CarrierSensor):
    """Sensor entity that reports indoor airflow in CFM."""

    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_FEET_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize an airflow sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="Airflow",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update airflow attrs from coordinator data."""
        if self.carrier_system.status.airflow_cfm is None:
            self._attr_available = False
            return
        self._attr_native_value = int(self.carrier_system.status.airflow_cfm)
        self._attr_available = True


class StaticPressureSensor(CarrierSensor):
    """Sensor entity that reports system static pressure."""

    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.INH2O
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a static pressure sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="Static Pressure",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update static pressure attrs from coordinator data."""
        self._attr_native_value = self.carrier_system.status.static_pressure
        self._attr_available = self._attr_native_value is not None


class OutdoorUnitOperationalStatusSensor(CarrierSensor):
    """Sensor for outdoor unit operational status and related raw details."""

    _attr_icon = "mdi:hvac"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize an outdoor unit operational status sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="ODU Status",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update outdoor operational status attrs from coordinator data."""
        value = self.carrier_system.status.outdoor_unit_operational_status
        if value is None:
            self._attr_available = False
        elif isinstance(value, str) and value.isdigit():
            self._attr_native_value = "on"
            self._attr_available = True
        else:
            self._attr_native_value = value
            self._attr_available = True
        status_raw = self.carrier_system.status.raw
        if status_raw is None:
            return
        outdoor_unit_attributes = status_raw.get("odu")
        if isinstance(outdoor_unit_attributes, Mapping):
            self._attr_extra_state_attributes = dict(outdoor_unit_attributes)


class IndoorUnitOperationalStatusSensor(CarrierSensor):
    """Sensor for indoor unit operational status and related raw details."""

    _attr_icon = "mdi:hvac"

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize an indoor unit operational status sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="IDU Status",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update indoor operational status attrs from coordinator data."""
        self._attr_native_value = self.carrier_system.status.indoor_unit_operational_status
        self._attr_available = self._attr_native_value is not None
        status_raw = self.carrier_system.status.raw
        if status_raw is None:
            self._attr_extra_state_attributes = {}
            return
        indoor_unit_attributes = status_raw.get("idu")
        if isinstance(indoor_unit_attributes, Mapping):
            self._attr_extra_state_attributes = dict(indoor_unit_attributes)
        else:
            self._attr_extra_state_attributes = {}


class OutdoorUnitVarSensor(CarrierSensor):
    """Sensor for variable-capacity outdoor unit percentage output."""

    _attr_icon = "mdi:percent-box"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a variable outdoor unit percentage sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name="ODU Var",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update outdoor unit variable rate.

        Non-numeric values such as ``'off'`` are treated as 0 % so the sensor
        stays available while the unit is idle rather than going unavailable.

        """
        value = self.carrier_system.status.outdoor_unit_operational_status
        if value is None or not isinstance(value, str):
            self._attr_available = False
            return
        if isinstance(value, str) and value == "off":
            self._attr_native_value = 0.0
            self._attr_available = True
            return
        try:
            self._attr_native_value = float(value)
            self._attr_available = True
        except ValueError:
            self._attr_available = False
