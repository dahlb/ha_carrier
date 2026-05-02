"""Expose Carrier thermostat zones as Home Assistant climate entities."""

from __future__ import annotations

from datetime import datetime
from functools import partial
import logging
from typing import Any

from carrier_api import ActivityTypes, ConfigZoneActivity, FanModes, SystemModes, TemperatureUnits
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_HALVES,
    PRECISION_WHOLE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConfigEntryCarrier
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierZoneEntity
from .const import CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS, FAN_AUTO
from .util import has_cool, has_fan, has_heat

_LOGGER: logging.Logger = logging.getLogger(__name__)

BASE_SUPPORT_FLAGS: ClimateEntityFeature = (
    ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.PRESET_MODE
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntryCarrier,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register thermostat entities for every configured zone.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier integration config entry.
        async_add_entities: Callback used to register entities.
    """
    _LOGGER.debug("setting up climate entry")
    infinite_hold = config_entry.options.get(CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS)
    coordinator = config_entry.runtime_data
    entities: list[Thermostat] = []
    for carrier_system in coordinator.systems:
        support_flags = BASE_SUPPORT_FLAGS
        hvac_modes: list[HVACMode] = [
            HVACMode.OFF,
        ]
        if has_fan(carrier_system):
            support_flags |= ClimateEntityFeature.FAN_MODE
            hvac_modes.append(HVACMode.FAN_ONLY)
        if has_cool(carrier_system):
            hvac_modes.append(HVACMode.COOL)
        if has_heat(carrier_system):
            hvac_modes.append(HVACMode.HEAT)
        if has_cool(carrier_system) and has_heat(carrier_system):
            support_flags |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
            hvac_modes.append(HVACMode.HEAT_COOL)
        entities.extend(
            Thermostat(
                coordinator=coordinator,
                system_serial=carrier_system.profile.serial,
                infinite_hold=infinite_hold,
                zone_api_id=zone.api_id,
                support_flags=support_flags,
                hvac_modes=hvac_modes,
            )
            for zone in carrier_system.config.zones
        )
    async_add_entities(entities)


class CarrierClimate(CarrierZoneEntity, ClimateEntity):
    """Shared Carrier base class for zone-backed climate entities."""

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        zone_api_id: str,
        unique_id_suffix: str,
    ) -> None:
        """Initialize a zone-backed Carrier climate entity.

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
        self._sync_entity_attrs()

    def _current_activity(self) -> ConfigZoneActivity | None:
        """Return the current Carrier activity profile for this zone.

        Returns:
            ConfigZoneActivity | None: Activity associated with current zone state.
        """
        activity_type = self._config_zone.hold_activity or self._status_zone.current_activity
        return self._config_zone.find_activity(activity_type)

    def _preset_mode(self) -> str | None:
        """Return the preset that best matches current zone setpoints.

        Returns:
            str | None: Matching activity type or API-reported fallback.
        """
        actual_heat = self._status_zone.heat_set_point
        actual_cool = self._status_zone.cool_set_point
        for activity in self._config_zone.activities:
            if activity.heat_set_point == actual_heat and activity.cool_set_point == actual_cool:
                return activity.type.value

        _LOGGER.debug(
            (
                "Zone %s: No activity matched setpoints (heat=%s, cool=%s). "
                "Falling back to API activity: %s"
            ),
            self._config_zone.name,
            actual_heat,
            actual_cool,
            self._status_zone.current_activity,
        )
        current_activity = self._current_activity()
        if current_activity is None:
            _LOGGER.debug(
                "Zone %s: Current activity %s was not found in the zone config",
                self._config_zone.name,
                self._status_zone.current_activity,
            )
            return self._status_zone.current_activity
        return current_activity.type.value

    def _update_entity_attrs(self) -> None:
        """Update climate attrs from coordinator data."""
        if self.carrier_system.status.temperature_unit == TemperatureUnits.FAHRENHEIT:
            temperature_unit = UnitOfTemperature.FAHRENHEIT
        else:
            temperature_unit = UnitOfTemperature.CELSIUS

        hvac_mode: HVACMode | None
        match self.carrier_system.config.mode:
            case SystemModes.COOL.value:
                hvac_mode = HVACMode.COOL
            case SystemModes.HEAT.value:
                hvac_mode = HVACMode.HEAT
            case SystemModes.OFF.value:
                hvac_mode = HVACMode.OFF
            case SystemModes.AUTO.value:
                hvac_mode = HVACMode.HEAT_COOL
            case SystemModes.FAN_ONLY.value:
                hvac_mode = HVACMode.FAN_ONLY
            case _:
                hvac_mode = None

        self._attr_current_humidity = self._status_zone.humidity
        self._attr_current_temperature = self._status_zone.temperature
        self._attr_temperature_unit = temperature_unit
        self._attr_hvac_mode = hvac_mode
        if hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif hvac_mode == HVACMode.FAN_ONLY:
            if self._status_zone.fan == FanModes.OFF:
                self._attr_hvac_action = HVACAction.IDLE
            else:
                self._attr_hvac_action = HVACAction.FAN
        elif self._status_zone.conditioning is None or self._status_zone.conditioning == "idle":
            self._attr_hvac_action = HVACAction.IDLE
        elif "heat" in self._status_zone.conditioning:
            self._attr_hvac_action = HVACAction.HEATING
        elif "cool" in self._status_zone.conditioning:
            self._attr_hvac_action = HVACAction.COOLING
        elif self._status_zone.fan == FanModes.OFF:
            self._attr_hvac_action = HVACAction.IDLE
        else:
            self._attr_hvac_action = HVACAction.FAN

        if temperature_unit == UnitOfTemperature.CELSIUS:
            self._attr_target_temperature_step = PRECISION_HALVES
        else:
            self._attr_target_temperature_step = PRECISION_WHOLE
        self._attr_target_temperature = None
        self._attr_target_temperature_high = None
        self._attr_target_temperature_low = None
        if hvac_mode == HVACMode.HEAT:
            self._attr_target_temperature = self._status_zone.heat_set_point
        elif hvac_mode == HVACMode.COOL:
            self._attr_target_temperature = self._status_zone.cool_set_point
        elif hvac_mode == HVACMode.HEAT_COOL:
            self._attr_target_temperature_high = self._status_zone.cool_set_point
            self._attr_target_temperature_low = self._status_zone.heat_set_point

        if self.carrier_system.config.humidifier_enabled:
            self._attr_target_humidity = self.carrier_system.config.humidifier_heat_target
        else:
            self._attr_target_humidity = None

        self._attr_preset_mode = self._preset_mode()
        current_activity = self._current_activity()
        if current_activity is None:
            _LOGGER.debug(
                "Zone %s: Current activity %s unavailable while reading fan mode",
                self._config_zone.name,
                self._status_zone.current_activity,
            )
            self._attr_fan_mode = None
        elif current_activity.fan == FanModes.OFF:
            self._attr_fan_mode = FAN_AUTO
        else:
            self._attr_fan_mode = current_activity.fan.value

        hold_activity_name = (
            self._config_zone.hold_activity.value if self._config_zone.hold_activity else None
        )
        self._attr_extra_state_attributes = {
            "conditioning": self._status_zone.conditioning,
            "status_mode": self.carrier_system.status.mode,
            "blower_rpm": self.carrier_system.status.blower_rpm,
            "damper_position": self._status_zone.damper_position,
            "hold_activity": hold_activity_name,
            "hold_until": self._config_zone.hold_until,
            "next_activity_time": self._config_zone.next_activity_time(),
        }
        self._attr_available = True


class Thermostat(CarrierClimate):
    """Climate entity that controls a single Carrier zone thermostat."""

    _enable_turn_on_off_backwards_compatibility = False
    _attr_max_humidity = 45
    _attr_min_humidity = 0

    def __init__(
        self,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str,
        infinite_hold: bool,
        zone_api_id: str,
        support_flags: ClimateEntityFeature,
        hvac_modes: list[HVACMode],
    ) -> None:
        """Initialize thermostat state and supported controls for one zone.

        Args:
            coordinator: Coordinator that provides Carrier system and zone state.
            system_serial: Carrier system serial for this thermostat.
            infinite_hold: Whether manual holds should be open-ended.
            zone_api_id: Carrier API identifier for the represented zone.
            support_flags: Climate entity features supported by the system.
            hvac_modes: HVAC modes supported by the system.
        """
        _LOGGER.debug("infinite_hold: %s", infinite_hold)
        self.infinite_hold = infinite_hold
        super().__init__(
            entity_name="",  # Climate Entity will just be the Zone Name
            coordinator=coordinator,
            system_serial=system_serial,
            zone_api_id=zone_api_id,
            unique_id_suffix="thermostat",
        )
        self._attr_supported_features = support_flags
        self._attr_fan_modes = [
            fan_mode.value for fan_mode in [FanModes.LOW, FanModes.MED, FanModes.HIGH]
        ]
        self._attr_fan_modes.append(FAN_AUTO)
        self._attr_hvac_modes = hvac_modes
        self._attr_preset_modes = [activity.type.value for activity in self._config_zone.activities]
        self._attr_preset_modes.append("resume")
        if self.carrier_system.config.humidifier_enabled:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_HUMIDITY

    async def async_set_humidity(self, humidity: int) -> None:
        """Set and normalize a new target humidity value.

        Args:
            humidity: Requested target humidity percentage.
        """
        _LOGGER.debug("Setting target humidity to %s", humidity)
        if humidity > 45:
            humidity = 45
            _LOGGER.debug("Setting target humidity to max heating of 45")
        rounded_humidity = int(humidity / 5) * 5
        _LOGGER.debug(
            "Setting target humidity to api acceptable multiple of 5 %s",
            rounded_humidity,
        )
        await self.coordinator.async_perform_api_call(
            "set humidity",
            partial(
                self.coordinator.api_connection.set_config_heat_humidity,
                system_serial=self.carrier_system.profile.serial,
                humidity_target=rounded_humidity,
            ),
        )
        self.carrier_system.config.humidifier_heat_target = rounded_humidity
        self._write_local_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the Carrier system mode from a Home Assistant HVAC mode.

        Args:
            hvac_mode: Requested Home Assistant HVAC mode.

        Raises:
            ValueError: Raised when the provided mode is unsupported.
        """
        _LOGGER.debug("set_hvac_mode; hvac_mode: %s", hvac_mode)
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
        await self.coordinator.async_perform_api_call(
            "set hvac mode",
            partial(
                self.coordinator.api_connection.set_config_mode,
                system_serial=self.carrier_system.profile.serial,
                mode=mode,
            ),
        )
        self.carrier_system.config.mode = mode.value
        self._write_local_state()

    @property
    def _hold_until(self) -> datetime | None:
        """Return hold end time based on integration hold preference.

        Returns:
            datetime | None: Next schedule transition or None for an indefinite hold.
        """
        _LOGGER.debug(
            "infinite_hold: %s; holding until: %s",
            self.infinite_hold,
            self._config_zone.next_activity_time(),
        )
        if not self.infinite_hold:
            return self._config_zone.next_activity_time()
        return None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Apply a preset activity or resume scheduled programming.

        Args:
            preset_mode: Requested preset mode or the special "resume" value.
        """
        _LOGGER.debug("set_preset_mode; preset_mode: %s", preset_mode)
        if preset_mode == "resume":
            await self.coordinator.async_perform_api_call(
                "resume schedule",
                partial(
                    self.coordinator.api_connection.resume_schedule,
                    system_serial=self.carrier_system.profile.serial,
                    zone_id=self.zone_api_id,
                ),
            )
            self.coordinator.data_flush = True
            await self.coordinator.async_refresh()
            return

        activity_type = ActivityTypes(preset_mode.strip().lower())
        selected_activity = self._config_zone.find_activity(activity_type)
        hold_until_sent = self._hold_until
        await self.coordinator.async_perform_api_call(
            "set preset mode",
            partial(
                self.coordinator.api_connection.set_config_hold,
                system_serial=self.carrier_system.profile.serial,
                zone_id=self.zone_api_id,
                activity_type=activity_type,
                hold_until=hold_until_sent,
            ),
        )
        self._config_zone.hold = True
        self._config_zone.hold_activity = activity_type
        self._config_zone.hold_until = hold_until_sent
        if selected_activity is not None:
            self._status_zone.heat_set_point = selected_activity.heat_set_point
            self._status_zone.cool_set_point = selected_activity.cool_set_point
        self._write_local_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed behavior for the current activity profile.

        Args:
            fan_mode: Requested fan mode label from Home Assistant.

        Raises:
            HomeAssistantError: Raised when the current activity is unavailable.
        """
        _LOGGER.debug("set_fan_mode; fan_mode: %s", fan_mode)
        selected_fan_mode = FanModes.OFF if fan_mode == FAN_AUTO else FanModes(fan_mode)
        current_activity = self._current_activity()
        if current_activity is None:
            raise HomeAssistantError("Current activity unavailable, try again later")
        await self.coordinator.async_perform_api_call(
            "set fan mode",
            partial(
                self.coordinator.api_connection.update_fan,
                system_serial=self.carrier_system.profile.serial,
                zone_id=self.zone_api_id,
                activity_type=current_activity.type,
                fan_mode=selected_fan_mode,
            ),
        )
        current_activity.fan = selected_fan_mode
        self._write_local_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Update target setpoints and apply a manual hold.

        Args:
            **kwargs: Home Assistant temperature arguments.

        Raises:
            HomeAssistantError: Raised when the manual activity profile cannot be resolved.
        """
        _LOGGER.debug("set_temperature; kwargs: %s", kwargs)
        heat_set_point = kwargs.get(ATTR_TARGET_TEMP_LOW)
        cool_set_point = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        temperature = kwargs.get(ATTR_TEMPERATURE)
        manual_activity = self._config_zone.find_activity(ActivityTypes.MANUAL)
        if manual_activity is None:
            raise HomeAssistantError("Manual activity unavailable, try again later")

        hold_until_sent = self._hold_until

        if self.carrier_system.config.mode == SystemModes.COOL.value:
            heat_set_point = manual_activity.heat_set_point
            cool_set_point = temperature or cool_set_point
        elif self.carrier_system.config.mode == SystemModes.HEAT.value:
            heat_set_point = temperature or heat_set_point
            cool_set_point = manual_activity.cool_set_point

        if heat_set_point is None or cool_set_point is None:
            raise HomeAssistantError(
                "Both heat and cool set points must be resolved before applying a manual hold"
            )

        fan_mode = manual_activity.fan

        _LOGGER.debug(
            "set_temperature; heat_set_point: %s, cool_set_point: %s, fan_mode: %s",
            heat_set_point,
            cool_set_point,
            fan_mode,
        )

        await self.coordinator.async_perform_api_call(
            "set manual activity",
            partial(
                self.coordinator.api_connection.set_config_manual_activity,
                system_serial=self.carrier_system.profile.serial,
                zone_id=self.zone_api_id,
                heat_set_point=str(heat_set_point),
                cool_set_point=str(cool_set_point),
                fan_mode=fan_mode,
            ),
        )
        await self.coordinator.async_perform_api_call(
            "set manual hold",
            partial(
                self.coordinator.api_connection.set_config_hold,
                system_serial=self.carrier_system.profile.serial,
                zone_id=self.zone_api_id,
                activity_type=ActivityTypes.MANUAL,
                hold_until=hold_until_sent,
            ),
        )

        self._config_zone.hold = True
        self._config_zone.hold_activity = ActivityTypes.MANUAL
        self._config_zone.hold_until = hold_until_sent
        manual_activity.cool_set_point = cool_set_point
        manual_activity.heat_set_point = heat_set_point
        self._status_zone.cool_set_point = cool_set_point
        self._status_zone.heat_set_point = heat_set_point
        self._write_local_state()
