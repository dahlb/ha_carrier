"""Expose Carrier telemetry, energy, and status sensors to Home Assistant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from carrier_api import EnergyPeriod, EnergyUsageMetric, StatusUnit, System, TemperatureUnits
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ConfigEntryCarrier
from .coordinator import CarrierDataUpdateCoordinator
from .entity import CarrierEntity, CarrierZoneEntity, async_add_entities_for_systems
from .util import TIMESTAMP_TYPES

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Read-only platform: the coordinator fetches data centrally, so entity updates
# never call Carrier and need no limit.
PARALLEL_UPDATES = 0


def start_of_yesterday() -> datetime:
    """Return local midnight at the start of yesterday.

    Carrier's ``day1`` energy period is the previous whole day, so its total
    resets when that day rolls over.

    Returns:
        datetime: Timezone-aware start of the previous local day.
    """
    return dt_util.start_of_local_day(dt_util.now().date() - timedelta(days=1))


def start_of_last_month() -> datetime:
    """Return local midnight on the first day of the previous month.

    Carrier's ``month1`` energy period is the previous whole month, so its
    total resets when that month rolls over.

    Returns:
        datetime: Timezone-aware start of the previous local month.
    """
    first_of_this_month = dt_util.now().date().replace(day=1)
    return dt_util.start_of_local_day((first_of_this_month - timedelta(days=1)).replace(day=1))


def _outdoor_unit(system: System) -> StatusUnit | None:
    """Return the system's mapped outdoor unit status, if Carrier reported one.

    Args:
        system: Carrier system.

    Returns:
        StatusUnit | None: Outdoor unit status.
    """
    return system.status.outdoor_unit


def _has_outdoor_unit(system: System) -> bool:
    """Return whether the system's profile lists an outdoor unit.

    Args:
        system: Carrier system.

    Returns:
        bool: True for heat pumps and air conditioners.
    """
    return system.profile.outdoor_unit_type not in (None, "", "none")


@dataclass(frozen=True, kw_only=True)
class CarrierUnitSensorDescription(SensorEntityDescription):
    """Describes a numeric reading from a Carrier indoor or outdoor unit.

    Attributes:
        value_fn: Returns the reading for a system, or None when Carrier has
            not reported it.
        outdoor_only: Create the sensor only for systems with an outdoor unit.
        system_temperature_unit: The reading is in the system's configured
            temperature unit (Fahrenheit or Celsius), which sets the native unit.
    """

    value_fn: Callable[[System], float | int | None]
    outdoor_only: bool = False
    system_temperature_unit: bool = False


def _unit_value(
    unit_fn: Callable[[System], StatusUnit | None],
    field: Callable[[StatusUnit], float | int | None],
) -> Callable[[System], float | int | None]:
    """Build a value function that reads one field from a unit, if present.

    Args:
        unit_fn: Returns the indoor or outdoor unit for a system.
        field: Reads the value from the unit.

    Returns:
        Callable[[System], float | int | None]: The value function.
    """

    def _value(system: System) -> float | int | None:
        """Return the unit field, or None when the unit is missing.

        Args:
            system: Carrier system.

        Returns:
            float | int | None: The reading.
        """
        unit = unit_fn(system)
        return None if unit is None else field(unit)

    return _value


def _blower_speed(system: System) -> float | int | None:
    """Return the indoor blower speed, falling back to the top-level status field.

    Args:
        system: Carrier system.

    Returns:
        float | int | None: Blower speed in rpm.
    """
    indoor_unit = system.status.indoor_unit
    if indoor_unit is not None and indoor_unit.blower_rpm is not None:
        return indoor_unit.blower_rpm
    return system.status.blower_rpm


# Outdoor-unit refrigerant readings arrive only in diagnostic-device-info
# websocket messages, so they stay unavailable on systems that never send those;
# hence disabled by default. Temperatures follow the system's configured unit.
UNIT_SENSORS: tuple[CarrierUnitSensorDescription, ...] = (
    CarrierUnitSensorDescription(
        key="blower_speed",
        translation_key="blower_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_blower_speed,
    ),
    CarrierUnitSensorDescription(
        key="outdoor_fan_speed",
        translation_key="outdoor_fan_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.compressor_rpm),
    ),
    CarrierUnitSensorDescription(
        key="outdoor_coil_temperature",
        translation_key="outdoor_coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        system_temperature_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.outdoor_coil_temperature),
    ),
    CarrierUnitSensorDescription(
        key="discharge_temperature",
        translation_key="discharge_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        system_temperature_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.discharge_temperature),
    ),
    CarrierUnitSensorDescription(
        key="suction_temperature",
        translation_key="suction_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        system_temperature_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.suction_temperature),
    ),
    CarrierUnitSensorDescription(
        key="suction_pressure",
        translation_key="suction_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.suction_pressure),
    ),
    CarrierUnitSensorDescription(
        # A temperature difference, not a temperature: no device class, so Home
        # Assistant does not apply an absolute °F/°C conversion to it.
        key="suction_superheat",
        translation_key="suction_superheat",
        system_temperature_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.suction_superheat),
    ),
    CarrierUnitSensorDescription(
        key="line_voltage",
        translation_key="line_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        outdoor_only=True,
        value_fn=_unit_value(_outdoor_unit, lambda unit: unit.line_voltage),
    ),
)


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
    async_add_entities_for_systems(
        config_entry,
        async_add_entities,
        lambda carrier_system: _system_entities(coordinator, carrier_system),
    )


def _system_entities(
    coordinator: CarrierDataUpdateCoordinator,
    carrier_system: System,
) -> list[SensorEntity]:
    """Build this platform's entities for one Carrier system.

    Args:
        coordinator: Coordinator that supplies Carrier system data.
        carrier_system: Carrier system to build entities for.

    Returns:
        list[SensorEntity]: Entities for the system.
    """
    entities: list[SensorEntity] = []
    entities.extend(
        [
            OutdoorTemperatureSensor(
                coordinator=coordinator, system_serial=carrier_system.profile.serial
            ),
            FilterUsedSensor(coordinator=coordinator, system_serial=carrier_system.profile.serial),
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
    # The profile only says "furnace"; the status payload names the staging.
    indoor_unit = carrier_system.status.indoor_unit
    if indoor_unit is not None and indoor_unit.type == "furnacemodulating":
        entities.append(
            IndoorUnitVarSensor(
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
    for electric_metric in carrier_system.energy.enabled_usage_metrics():
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
    fuel_type = carrier_system.config.fuel_type
    if (
        carrier_system.energy.is_usage_metric_enabled(EnergyUsageMetric.GAS)
        and fuel_type is not None
    ):
        entities.append(
            GasMeasurementSensor(
                coordinator=coordinator,
                system_serial=carrier_system.profile.serial,
                fuel_type=fuel_type,
            )
        )
        if fuel_type == "propane":
            entities.append(
                PropaneMeasurementSensor(
                    coordinator=coordinator, system_serial=carrier_system.profile.serial
                )
            )
    entities.extend(
        CarrierUnitSensor(coordinator, carrier_system.profile.serial, description)
        for description in UNIT_SENSORS
        if not description.outdoor_only or _has_outdoor_unit(carrier_system)
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
                ZoneDamperPositionSensor(
                    coordinator=coordinator,
                    system_serial=carrier_system.profile.serial,
                    zone_api_id=zone.api_id,
                ),
                ZoneConditioningSensor(
                    coordinator=coordinator,
                    system_serial=carrier_system.profile.serial,
                    zone_api_id=zone.api_id,
                ),
            ]
        )
    return entities


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
        """Default to unavailable so concrete sensors must opt in to data.

        Each concrete sensor subclass overrides this to populate
        ``_attr_native_value`` (and unit / state class metadata where it
        depends on system configuration) and to flip ``_attr_available`` to
        True once a usable reading is present.
        """
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

    _attr_translation_key = "zone_humidity"
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

    _attr_translation_key = "fuel_usage_year_to_date"
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
        self.metric = EnergyUsageMetric.GAS
        self.fuel_type = fuel_type
        self._attr_translation_placeholders = {"fuel_type": fuel_type.capitalize()}
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
        value: float | int | None = self.carrier_system.energy.value_for_period_metric(
            EnergyPeriod.YEAR_1, self.metric
        )
        if value is None:
            _LOGGER.debug(
                "%s missing in year1 energy period for system %s; marking unavailable",
                self.metric,
                self._system_serial,
            )
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

    _attr_translation_key = "propane_consumption_year_to_date"
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
        self.metric = EnergyUsageMetric.GAS
        super().__init__(
            entity_name="Propane Consumption Year to Date",
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        """Update propane usage attrs from coordinator data."""
        value = self.carrier_system.energy.value_for_period_metric(EnergyPeriod.YEAR_1, self.metric)
        if value is None:
            _LOGGER.debug(
                "%s missing in year1 energy period for system %s; marking unavailable",
                self.metric,
                self._system_serial,
            )
            self._attr_available = False
            return

        # Convert kBTU to gallons (1 gallon LPG = 91,690 BTU, so divide by 91.69)
        self._attr_native_value = value / 91.69
        self._attr_available = True


class YearlyEnergyMeasurementSensor(CarrierSensor):
    """Yearly electrical energy consumption sensor for a Carrier metric."""

    _attr_translation_key = "energy_year_to_date"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        metric: EnergyUsageMetric,
    ) -> None:
        """Initialize a yearly energy measurement sensor.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Carrier API energy metric to expose.
        """
        self.metric = metric
        self._attr_translation_placeholders = {"metric": metric.label}
        super().__init__(
            entity_name=f"{metric.label} Energy Year to Date",
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=f"{metric.value} Energy Year to Date",
        )

    def _update_entity_attrs(self) -> None:
        """Update yearly energy attrs from coordinator data."""
        value = self.carrier_system.energy.value_for_period_metric(EnergyPeriod.YEAR_1, self.metric)
        self._attr_native_value = value
        self._attr_available = value is not None


class DailyEnergyMeasurementSensor(CarrierSensor):
    """Sensor for yesterday's energy usage by Carrier metric."""

    _attr_translation_key = "energy_yesterday"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        metric: EnergyUsageMetric,
    ) -> None:
        """Initialize a daily energy sensor for a specific metric.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Carrier API energy metric to expose.
        """
        self.metric = metric
        self._attr_translation_placeholders = {"metric": metric.label}
        super().__init__(
            entity_name=f"{metric.label} Energy Yesterday",
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=f"{metric.value} Energy Yesterday",
        )

    def _update_entity_attrs(self) -> None:
        """Update daily energy attrs from coordinator data."""
        value = self.carrier_system.energy.value_for_period_metric(EnergyPeriod.DAY_1, self.metric)
        self._attr_native_value = value
        # A TOTAL sensor without last_reset makes every day-to-day drop count as
        # negative consumption in long-term statistics.
        self._attr_last_reset = start_of_yesterday()
        self._attr_available = value is not None


class MonthlyEnergyMeasurementSensor(CarrierSensor):
    """Sensor for last month's energy usage by Carrier metric."""

    _attr_translation_key = "energy_last_month"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        metric: EnergyUsageMetric,
    ) -> None:
        """Initialize a monthly energy sensor for a specific metric.

        Args:
            coordinator: Coordinator that provides energy payloads.
            system_serial: Carrier system serial for this entity.
            metric: Carrier API energy metric to expose.
        """
        self.metric = metric
        self._attr_translation_placeholders = {"metric": metric.label}
        super().__init__(
            entity_name=f"{metric.label} Energy Last Month",
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=f"{metric.value} Energy Last Month",
        )

    def _update_entity_attrs(self) -> None:
        """Update monthly energy attrs from coordinator data."""
        value = self.carrier_system.energy.value_for_period_metric(
            EnergyPeriod.MONTH_1, self.metric
        )
        self._attr_native_value = value
        self._attr_last_reset = start_of_last_month()
        self._attr_available = value is not None


class ZoneTemperatureSensor(CarrierZoneSensor):
    """Sensor entity that reports current temperature for a specific zone."""

    _attr_translation_key = "zone_temperature"
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

    _attr_translation_key = "outdoor_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    # Outdoor temperature seems to always send as Fahrenheit regardless
    # of what `temperature_unit` is set to
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
        self._attr_native_value = self.carrier_system.status.outdoor_temperature
        self._attr_available = self._attr_native_value is not None


class FilterUsedSensor(CarrierSensor):
    """Filter life sensor represented as a percentage."""

    _attr_translation_key = "filter_remaining"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

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

    _attr_translation_key = "humidifier_remaining"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

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

    _attr_translation_key = "uv_lamp_remaining"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

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
    # Troubleshooting aids that change on every update; enable when debugging.
    _attr_entity_registry_enabled_default = False

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
        self._attr_translation_key = f"{timestamp_type}_last_updated"
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

    _attr_translation_key = "airflow"
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_FEET_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT

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
        indoor_unit = self.carrier_system.status.indoor_unit
        value = indoor_unit.airflow_cfm if indoor_unit is not None else None
        if value is None:
            value = self.carrier_system.status.airflow_cfm
        if value is None:
            self._attr_available = False
            return
        self._attr_native_value = int(value)
        self._attr_available = True


class StaticPressureSensor(CarrierSensor):
    """Sensor entity that reports system static pressure."""

    _attr_translation_key = "static_pressure"
    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.INH2O
    # Home Assistant's unit systems would otherwise convert this to psi or kPa,
    # which makes duct static pressure unreadable.
    _attr_suggested_unit_of_measurement = UnitOfPressure.INH2O
    _attr_state_class = SensorStateClass.MEASUREMENT

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
        indoor_unit = self.carrier_system.status.indoor_unit
        value = indoor_unit.static_pressure if indoor_unit is not None else None
        if value is None:
            value = self.carrier_system.status.static_pressure
        # Both sources are native inH2O readings from Carrier. The indoor unit
        # value is the mapped unit-status field; the top-level status value is
        # the system-status fallback used when that mapped field is absent.
        self._attr_native_value = value
        self._attr_available = self._attr_native_value is not None


class OutdoorUnitOperationalStatusSensor(CarrierSensor):
    """Sensor for the outdoor unit's operational status."""

    _attr_translation_key = "odu_status"

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


class IndoorUnitOperationalStatusSensor(CarrierSensor):
    """Sensor for the indoor unit's operational status."""

    _attr_translation_key = "idu_status"

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


class OutdoorUnitVarSensor(CarrierSensor):
    """Sensor for variable-capacity outdoor unit percentage output."""

    _attr_translation_key = "odu_var"
    _entity_name = "ODU Var"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a variable-capacity unit percentage sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
        """
        super().__init__(
            entity_name=self._entity_name,
            coordinator=coordinator,
            system_serial=system_serial,
        )

    def _operational_status(self) -> str | None:
        """Return the unit operational status that carries the percentage.

        Returns:
            Carrier's operational status for the outdoor unit.
        """
        return self.carrier_system.status.outdoor_unit_operational_status

    def _update_entity_attrs(self) -> None:
        """Update the unit's variable rate.

        Non-numeric values such as ``'off'`` are treated as 0 % so the sensor
        stays available while the unit is idle rather than going unavailable.

        """
        value = self._operational_status()
        if value is None or not isinstance(value, str):
            self._attr_available = False
            return
        if isinstance(value, str) and value in {"off", "dehumidify"}:
            self._attr_native_value = 0.0
            self._attr_available = True
            return
        try:
            self._attr_native_value = float(value)
            self._attr_available = True
        except ValueError:
            self._attr_available = False


class IndoorUnitVarSensor(OutdoorUnitVarSensor):
    """Sensor for modulating furnace percentage output."""

    _attr_translation_key = "idu_var"

    _entity_name = "IDU Var"

    def _operational_status(self) -> str | None:
        """Return the unit operational status that carries the percentage.

        Returns:
            Carrier's operational status for the indoor unit.
        """
        return self.carrier_system.status.indoor_unit_operational_status


class CarrierUnitSensor(CarrierSensor):
    """Numeric reading from a Carrier indoor or outdoor unit, defined by a description."""

    entity_description: CarrierUnitSensorDescription

    def __init__(
        self,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        description: CarrierUnitSensorDescription,
    ) -> None:
        """Initialize a unit reading sensor.

        Args:
            coordinator: Coordinator that provides system telemetry.
            system_serial: Carrier system serial for this entity.
            description: Which reading to expose and how.
        """
        self.entity_description = description
        super().__init__(
            entity_name=description.key,
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=description.key,
        )

    def _update_entity_attrs(self) -> None:
        """Update the reading from coordinator data; unavailable until Carrier sends it."""
        if self.entity_description.system_temperature_unit:
            if self.carrier_system.status.temperature_unit == TemperatureUnits.FAHRENHEIT:
                self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
            else:
                self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_native_value = self.entity_description.value_fn(self.carrier_system)
        self._attr_available = self._attr_native_value is not None


class ZoneDamperPositionSensor(CarrierZoneSensor):
    """Sensor for how far open a zone's damper is."""

    _attr_translation_key = "zone_damper_position"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str
    ) -> None:
        """Initialize a zone damper position sensor.

        Args:
            coordinator: Coordinator that provides system and zone data.
            system_serial: Carrier system serial tied to the zone.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        super().__init__(
            entity_name="Damper Position",
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix="damper_position",
        )

    def _update_entity_attrs(self) -> None:
        """Update damper position attrs from coordinator data."""
        self._attr_native_value = self._status_zone.damper_position
        self._attr_available = self._attr_native_value is not None


class ZoneConditioningSensor(CarrierZoneSensor):
    """Sensor for the raw conditioning state Carrier reports for a zone.

    The climate entity's HVAC action already summarizes this, so the sensor is
    disabled by default; it keeps Carrier's finer-grained value (for example
    ``active_heat`` versus ``idle``) available for history and automations.
    """

    _attr_translation_key = "zone_conditioning"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str
    ) -> None:
        """Initialize a zone conditioning sensor.

        Args:
            coordinator: Coordinator that provides system and zone data.
            system_serial: Carrier system serial tied to the zone.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        super().__init__(
            entity_name="Conditioning",
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix="conditioning",
        )

    def _update_entity_attrs(self) -> None:
        """Update conditioning attrs from coordinator data."""
        self._attr_native_value = self._status_zone.conditioning
        self._attr_available = self._attr_native_value is not None
