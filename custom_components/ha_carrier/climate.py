"""Expose Carrier thermostat zones as Home Assistant climate entities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from functools import partial
import logging
from typing import Any

from carrier_api import ActivityTypes, ConfigZoneActivity, FanModes, SystemModes, TemperatureUnits
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
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
from .carrier_entity import CarrierEntity
from .const import CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS, FAN_AUTO

_LOGGER: logging.Logger = logging.getLogger(__name__)

SUPPORT_FLAGS = (
    ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    | ClimateEntityFeature.FAN_MODE
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

    Returns:
        None: Thermostat entities are registered through the callback.
    """
    _LOGGER.debug("setting up climate entry")
    infinite_hold = config_entry.options.get(CONF_INFINITE_HOLDS, DEFAULT_INFINITE_HOLDS)
    updater = config_entry.runtime_data
    entities = []
    for carrier_system in updater.systems:
        for zone in carrier_system.config.zones:
            entities.extend(
                [
                    Thermostat(
                        updater,
                        carrier_system.profile.serial,
                        infinite_hold=infinite_hold,
                        zone_api_id=zone.api_id,
                    ),
                ]
            )
    async_add_entities(entities)


class Thermostat(CarrierEntity, ClimateEntity):
    """Climate entity that controls a single Carrier zone thermostat."""

    _attr_supported_features = SUPPORT_FLAGS
    _enable_turn_on_off_backwards_compatibility = False
    _attr_max_humidity = 45
    _attr_min_humidity = 0

    def __init__(
        self,
        updater: CarrierDataUpdateCoordinator,
        system_serial: str,
        infinite_hold: bool,
        zone_api_id: str,
    ) -> None:
        """Initialize thermostat state and supported controls for one zone.

        Args:
            updater: Coordinator that provides Carrier system and zone state.
            system_serial: Carrier system serial for this thermostat.
            infinite_hold: Whether manual holds should be open-ended.
            zone_api_id: Carrier API identifier for the represented zone.
        """
        _LOGGER.debug("infinite_hold:%s", infinite_hold)
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
        self._attr_preset_modes = [activity.type.value for activity in self._config_zone.activities]
        self._attr_preset_modes.append("resume")
        if self.carrier_system.config.humidifier_enabled:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_HUMIDITY

    @property
    def current_humidity(self) -> int | None:
        """Return the latest humidity reading for this zone.

        Returns:
            int | None: Relative humidity percentage reported by Carrier.
        """
        return self._status_zone.humidity

    @property
    def current_temperature(self) -> float | None:
        """Return the latest ambient temperature for this zone.

        Returns:
            float | None: Current zone temperature.
        """
        return self._status_zone.temperature

    @property
    def temperature_unit(self) -> str:
        """Return the temperature unit used by the current system.

        Returns:
            str: Home Assistant temperature unit constant.
        """
        if self.carrier_system.status.temperature_unit == TemperatureUnits.FAHRENHEIT:
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Map Carrier system mode to a Home Assistant HVAC mode.

        Returns:
            HVACMode | None: Current mode translated for Home Assistant.
        """
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
    def hvac_action(self) -> HVACAction | None:
        """Infer the active HVAC action from zone runtime data.

        Returns:
            HVACAction | None: Heating, cooling, fan, idle, or off state.
        """
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._status_zone.conditioning is None or self._status_zone.conditioning == "idle":
            return HVACAction.IDLE
        if "heat" in self._status_zone.conditioning:
            return HVACAction.HEATING
        if "cool" in self._status_zone.conditioning:
            return HVACAction.COOLING
        if self._status_zone.fan == FanModes.OFF:
            return HVACAction.IDLE
        return HVACAction.FAN

    def _current_activity(self) -> ConfigZoneActivity:
        """Return the current Carrier activity profile for this zone.

        Returns:
            ConfigZoneActivity: Activity associated with current zone state.
        """
        return self._config_zone.find_activity(self._status_zone.current_activity)

    @property
    def target_temperature_step(self) -> float:
        """Return the smallest setpoint increment accepted by this system.

        Returns:
            float: 0.5 in Celsius mode, 1.0 in Fahrenheit mode.
        """
        if self.temperature_unit == UnitOfTemperature.CELSIUS:
            return PRECISION_HALVES
        return PRECISION_WHOLE

    @property
    def target_temperature(self) -> float | None:
        """Return the active single setpoint for heat-only or cool-only mode.

        Returns:
            float | None: Current heat or cool setpoint, depending on mode.
        """
        # Use actual setpoints from status, not config activity lookup
        # This fixes bug where API returns stale currentActivity but correct htsp/clsp
        if self.hvac_mode == HVACMode.HEAT:
            return self._status_zone.heat_set_point
        if self.hvac_mode == HVACMode.COOL:
            return self._status_zone.cool_set_point
        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return the active high setpoint in auto changeover mode.

        Returns:
            float | None: Cool setpoint when operating in heat/cool mode.
        """
        # Use actual setpoints from status, not config activity lookup
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return self._status_zone.cool_set_point
        return None

    @property
    def target_temperature_low(self) -> float | None:
        """Return the active low setpoint in auto changeover mode.

        Returns:
            float | None: Heat setpoint when operating in heat/cool mode.
        """
        # Use actual setpoints from status, not config activity lookup
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return self._status_zone.heat_set_point
        return None

    @property
    def target_humidity(self) -> float | None:
        """Return the configured humidifier heating target when supported.

        Returns:
            float | None: Target humidity percentage for heating mode.
        """
        if self.carrier_system.config.humidifier_enabled:
            return self.carrier_system.config.humidifier_heat_target
        return None

    @property
    def preset_mode(self) -> str | None:
        """Return the preset that best matches current zone setpoints.

        Returns:
            str | None: Matching activity type or API-reported fallback.
        """
        # Get actual setpoints from status (not from activity lookup)
        actual_heat = self._status_zone.heat_set_point
        actual_cool = self._status_zone.cool_set_point
        # Find which activity matches these setpoints
        for activity in self._config_zone.activities:
            if activity.heat_set_point == actual_heat and activity.cool_set_point == actual_cool:
                return activity.type.value
        # No match found - fall back to API's reported activity
        # This could happen during transitions or with custom setpoints
        _LOGGER.debug(
            "Zone %s: No activity matched setpoints (heat=%s, cool=%s). "
            "Falling back to API activity: %s",
            self._config_zone.name,
            actual_heat,
            actual_cool,
            self._current_activity().type.value,
        )
        return self._current_activity().type.value

    @property
    def fan_mode(self) -> str | None:
        """Return the user-facing fan mode for the current activity.

        Returns:
            str | None: Explicit fan speed or auto mode label.
        """
        if self._current_activity().fan == FanModes.OFF:
            return FAN_AUTO
        return self._current_activity().fan.value

    async def async_set_humidity(self, humidity: int) -> None:
        """Set and normalize a new target humidity value.

        Args:
            humidity: Requested target humidity percentage.

        Returns:
            None: State is updated locally after a successful API call.
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
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the Carrier system mode from a Home Assistant HVAC mode.

        Args:
            hvac_mode: Requested Home Assistant HVAC mode.

        Returns:
            None: State is updated locally after a successful API call.

        Raises:
            ValueError: Raised when the provided mode is unsupported.
        """
        _LOGGER.debug("set_hvac_mode; hvac_mode:%s", hvac_mode)
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
        self.async_write_ha_state()

    @property
    def _hold_until(self) -> datetime | None:
        """Return hold end time based on integration hold preference.

        Returns:
            datetime | None: Next schedule transition when finite holds are
            enabled, otherwise None for an indefinite hold.
        """
        _LOGGER.debug(
            "infinite_hold:%s; holding until:'%s'",
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

        Returns:
            None: Entity state is updated after applying the change.
        """
        _LOGGER.debug("set_preset_mode; preset_mode:%s", preset_mode)
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
        else:
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
            # Mirror the requested hold locally so the entity reflects the user's
            # selection immediately instead of waiting for the next coordinator poll.
            self._config_zone.hold = True
            self._config_zone.hold_activity = activity_type
            self._config_zone.hold_until = hold_until_sent
            if selected_activity is not None:
                self._status_zone.heat_set_point = selected_activity.heat_set_point
                self._status_zone.cool_set_point = selected_activity.cool_set_point
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed behavior for the current activity profile.

        Args:
            fan_mode: Requested fan mode label from Home Assistant.

        Returns:
            None: Entity state is updated after applying the change.

        Raises:
            HomeAssistantError: Raised when the current activity is unavailable.
        """
        _LOGGER.debug("set_fan_mode; fan_mode:%s", fan_mode)
        if fan_mode == FAN_AUTO:
            fan_mode = FanModes.OFF
        else:
            fan_mode = FanModes(fan_mode)
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
                fan_mode=fan_mode,
            ),
        )
        current_activity.fan = fan_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Update target setpoints and apply a manual hold.

        Args:
            **kwargs: Home Assistant temperature arguments, including optional
                low/high setpoints and single temperature values.

        Returns:
            None: Local status/config values are updated after successful writes.

        Raises:
            HomeAssistantError: Raised when the manual activity profile cannot
                be resolved for this zone.
        """
        _LOGGER.debug("set_temperature; kwargs:%s", kwargs)
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

        fan_mode = manual_activity.fan

        _LOGGER.debug(
            "set_temperature; heat_set_point:%s, cool_set_point:%s, fan_mode:%s",
            heat_set_point,
            cool_set_point,
            fan_mode,
        )
        # Apply setpoints before enabling the hold so a failed second write does
        # not leave the thermostat pinned to MANUAL with stale temperatures.
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
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Expose supplemental runtime and hold metadata.

        Returns:
            Mapping[str, Any] | None: Additional state attributes for diagnostics
            and UI display.
        """
        hold_activity_name = (
            self._config_zone.hold_activity.value if self._config_zone.hold_activity else None
        )
        return {
            "conditioning": self._status_zone.conditioning,
            "status_mode": self.carrier_system.status.mode,
            "blower_rpm": self.carrier_system.status.blower_rpm,
            "damper_position": self._status_zone.damper_position,
            "hold_activity": hold_activity_name,
            "hold_until": self._config_zone.hold_until,
            "next_activity_time": self._config_zone.next_activity_time(),
        }

    @property
    def available(self) -> bool:
        """Indicate whether zone status/config data can be resolved.

        Returns:
            bool: True when both status and config zone data are available.
        """
        # `find_activity(status.current_activity)` can transiently return None
        # during websocket/config synchronization. Availability should reflect
        # zone existence, not activity lookup success in that instant.
        try:
            return self._status_zone is not None and self._config_zone is not None
        except ValueError:
            return False
