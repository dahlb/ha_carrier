"""Create climate platform."""

from __future__ import annotations

from logging import Logger, getLogger

from collections.abc import Mapping
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
    PRECISION_HALVES,
    PRECISION_WHOLE,
)
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.config_entries import ConfigEntry

from carrier_api import (
    FanModes,
    SystemModes,
    TemperatureUnits,
    ActivityTypes,
    ConfigZoneActivity,
)

from .const import (
    DOMAIN,
    DATA_UPDATE_COORDINATOR,
    CONF_INFINITE_HOLDS,
    DEFAULT_INFINITE_HOLDS,
    FAN_AUTO,
)
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

_LOGGER: Logger = getLogger(__package__)

SUPPORT_FLAGS = (
    ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.PRESET_MODE
)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create climate platform."""
    _LOGGER.debug("setting up climate entry")
    infinite_hold = config_entry.options.get(
        CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS
    )
    updater: CarrierDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_UPDATE_COORDINATOR]
    entities = []
    for carrier_system in updater.systems:
        for zone in carrier_system.config.zones:
            entities.extend(
                [
                    Thermostat(
                        updater, carrier_system.profile.serial, infinite_hold=infinite_hold, zone_api_id=zone.api_id
                    ),
                ]
            )
    async_add_entities(entities)


class Thermostat(CarrierEntity, ClimateEntity):
    """Create thermostat."""
    _attr_supported_features = SUPPORT_FLAGS
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str, infinite_hold: bool, zone_api_id: str):
        """Create thermostat."""
        _LOGGER.debug(f"infinite_hold:{infinite_hold}")
        self.infinite_hold: bool = infinite_hold
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        self.entity_description = ClimateEntityDescription(
            key=f"#{system_serial}-zone{self.zone_api_id}-climate",
        )
        super().__init__(f"{self._config_zone.name}", updater, system_serial)
        self._attr_fan_modes = [
            fan_mode.value for fan_mode in [FanModes.LOW, FanModes.MED, FanModes.HIGH]
        ]
        self._attr_fan_modes.append(FAN_AUTO)
        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.FAN_ONLY,
            HVACMode.HEAT_COOL,
            HVACMode.HEAT,
            HVACMode.COOL,
        ]
        self._attr_preset_modes = [
            activity.type.value for activity in self._config_zone.activities
        ]
        self._attr_preset_modes.append("resume")

    @property
    def current_humidity(self) -> int | None:
        """Return current humidity."""
        return self._status_zone.humidity

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        return self._status_zone.temperature

    @property
    def temperature_unit(self) -> str:
        """Return temperature unit constant."""
        if (
            self.carrier_system.status.temperature_unit
            == TemperatureUnits.FAHRENHEIT
        ):
            return UnitOfTemperature.FAHRENHEIT
        else:
            return UnitOfTemperature.CELSIUS

    @property
    def hvac_mode(self) -> HVACMode | str | None:
        """Return hvac mode."""
        ha_mode = None
        match self.carrier_system.config.mode:
            case SystemModes.COOL.value:
                ha_mode = HVACMode.COOL
            case SystemModes.HEAT.value:
                ha_mode = HVACMode.HEAT
            case SystemModes.OFF.value:
                ha_mode = HVACMode.OFF
            case SystemModes.AUTO.value:
                ha_mode = HVACMode.HEAT_COOL
            case SystemModes.FAN_ONLY.value:
                ha_mode = HVACMode.FAN_ONLY
        return ha_mode

    @property
    def hvac_action(self) -> HVACAction | str | None:
        """Return hvac action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        elif self._status_zone.conditioning is None or self._status_zone.conditioning == "idle":
            return HVACAction.IDLE
        elif "heat" in self._status_zone.conditioning:
            return HVACAction.HEATING
        elif "cool" in self._status_zone.conditioning:
            return HVACAction.COOLING
        elif self._status_zone.fan == FanModes.OFF:
            return HVACAction.IDLE
        else:
            return HVACAction.FAN

    def _current_activity(self) -> ConfigZoneActivity:
        return self._config_zone.find_activity(self._status_zone.current_activity)

    @property
    def target_temperature_step(self) -> float:
        if self.temperature_unit == UnitOfTemperature.CELSIUS:
            return PRECISION_HALVES
        else:
            return PRECISION_WHOLE

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        if self.hvac_mode == HVACMode.HEAT:
            return self._current_activity().heat_set_point
        if self.hvac_mode == HVACMode.COOL:
            return self._current_activity().cool_set_point
        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return target temperature high."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return self._current_activity().cool_set_point
        return None

    @property
    def target_temperature_low(self) -> float | None:
        """Return target temperature low."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return self._current_activity().heat_set_point
        return None

    @property
    def preset_mode(self) -> str | None:
        """Return preset mode."""
        return self._current_activity().type.value

    @property
    def fan_mode(self) -> str | None:
        """Return fan mode."""
        if self._current_activity().fan == FanModes.OFF:
            return FAN_AUTO
        else:
            return self._current_activity().fan.value

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Update hvac mode."""
        _LOGGER.debug(f"set_hvac_mode; hvac_mode:{hvac_mode}")
        match hvac_mode.strip().lower():
            case HVACMode.COOL:
                mode = SystemModes.COOL
            case HVACMode.HEAT:
                mode = SystemModes.HEAT
            case HVACMode.OFF:
                mode = SystemModes.OFF
            case HVACMode.HEAT_COOL:
                mode = SystemModes.AUTO
            case HVACMode.FAN_ONLY:
                mode = SystemModes.FAN_ONLY
            case _:
                raise ValueError(f"unsupported mode: {hvac_mode}")
        self.carrier_system.config.mode = mode.value
        await self.coordinator.api_connection.set_config_mode(
            system_serial=self.carrier_system.profile.serial, mode=mode
        )

    @property
    def _hold_until(self):
        _LOGGER.debug(
            f"infinite_hold:{self.infinite_hold}; holding until:'{self._config_zone.next_activity_time()}'"
        )
        if not self.infinite_hold:
            return self._config_zone.next_activity_time()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        _LOGGER.debug(f"set_preset_mode; preset_mode:{preset_mode}")
        if preset_mode == "resume":
            await self.coordinator.api_connection.resume_schedule(
                system_serial=self.carrier_system.profile.serial,
                zone_id=self.zone_api_id,
            )
        else:
            activity_type = ActivityTypes(preset_mode.strip().lower())
            self._config_zone.hold = True
            self._config_zone.hold_activity = activity_type
            await self.coordinator.api_connection.set_config_hold(
                system_serial=self.carrier_system.profile.serial,
                zone_id=self.zone_api_id,
                activity_type=activity_type,
                hold_until=self._hold_until,
            )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        _LOGGER.debug(f"set_fan_mode; fan_mode:{fan_mode}")
        if fan_mode == FAN_AUTO:
            fan_mode = FanModes.OFF
        else:
            fan_mode = FanModes(fan_mode)
        self._current_activity().fan_mode = fan_mode
        await self.coordinator.api_connection.update_fan(
            system_serial=self.carrier_system.profile.serial,
            zone_id=self.zone_api_id,
            activity_type=self._current_activity().type,
            fan_mode=fan_mode,
        )

    async def async_set_temperature(self, **kwargs) -> None:
        """Set temperatures."""
        _LOGGER.debug(f"set_temperature; kwargs:{kwargs}")
        heat_set_point = kwargs.get(ATTR_TARGET_TEMP_LOW)
        cool_set_point = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        temperature = kwargs.get(ATTR_TEMPERATURE)
        manual_activity = self._config_zone.find_activity(ActivityTypes.MANUAL)

        if self.carrier_system.config.mode == SystemModes.COOL.value:
            heat_set_point = manual_activity.heat_set_point
            cool_set_point = temperature or cool_set_point
        elif self.carrier_system.config.mode == SystemModes.HEAT.value:
            heat_set_point = temperature or heat_set_point
            cool_set_point = manual_activity.cool_set_point

        fan_mode = manual_activity.fan
        manual_activity.cool_set_point = cool_set_point
        manual_activity.heat_set_point = heat_set_point

        _LOGGER.debug(
            f"set_temperature; heat_set_point:{heat_set_point}, cool_set_point:{cool_set_point}, fan_mode:{fan_mode}"
        )
        await self.coordinator.api_connection.set_config_hold(
            system_serial=self.carrier_system.profile.serial,
            zone_id=self.zone_api_id,
            activity_type=ActivityTypes.MANUAL,
            hold_until=self._hold_until,
        )
        await self.coordinator.api_connection.set_config_manual_activity(
            system_serial=self.carrier_system.profile.serial,
            zone_id=self.zone_api_id,
            heat_set_point=str(heat_set_point),
            cool_set_point=str(cool_set_point),
            fan_mode=fan_mode,
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return extra state attributes."""
        return {
            "conditioning": self._status_zone.conditioning,
            "status_mode": self.carrier_system.status.mode,
            "blower_rpm": self.carrier_system.status.blower_rpm,
            "damper_position": self._status_zone_raw.damper_position,
        }

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self._status_zone is not None and self._current_activity() is not None
