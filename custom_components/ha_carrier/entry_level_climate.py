"""Expose Carrier entry-level (Smart Thermostat) zones as climate entities."""

from __future__ import annotations

from functools import partial
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import DOMAIN

if TYPE_CHECKING:
    from carrier_api import EntryLevelSystem, EntryLevelZone

_LOGGER: logging.Logger = logging.getLogger(__name__)

# The device reports and accepts a lowercase mode string.
MODE_TO_HVAC: dict[str, HVACMode] = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "off": HVACMode.OFF,
    "auto": HVACMode.HEAT_COOL,
}
HVAC_TO_MODE: dict[HVACMode, str] = {
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.OFF: "off",
    HVACMode.HEAT_COOL: "auto",
}


def build_entry_level_entities(
    coordinator: CarrierDataUpdateCoordinator,
) -> list[EntryLevelThermostat]:
    """Build a climate entity for each entry-level zone the coordinator tracks.

    Args:
        coordinator: Coordinator that supplies Carrier entry-level data.

    Returns:
        A list of entry-level thermostat entities to register.
    """
    return [
        EntryLevelThermostat(coordinator, system.serial, zone.index)
        for system in coordinator.entry_level_systems
        for zone in system.zones
    ]


class EntryLevelThermostat(CoordinatorEntity[CarrierDataUpdateCoordinator], ClimateEntity):
    """Climate entity controlling one Carrier entry-level thermostat zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = PRECISION_WHOLE
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.HEAT_COOL,
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, serial: str, index: int) -> None:
        """Initialize an entry-level thermostat entity.

        Args:
            coordinator: Coordinator that supplies Carrier entry-level data.
            serial: Serial of the entry-level system this entity represents.
            index: Zone index within the entry-level system.
        """
        super().__init__(coordinator, serial)
        self._serial = serial
        self._index = index
        self._attr_unique_id = slugify(f"{serial}_{index}_entry_level_thermostat")
        system = self._system
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer="Carrier",
            model=system.model if system is not None else None,
            sw_version=system.firmware if system is not None else None,
            name=system.name if system is not None else serial,
        )

    @property
    def _system(self) -> EntryLevelSystem | None:
        """Return the coordinator's entry-level system for this entity."""
        return self.coordinator.entry_level_system(self._serial)

    @property
    def _zone(self) -> EntryLevelZone | None:
        """Return the coordinator's entry-level zone for this entity."""
        system = self._system
        if system is None:
            return None
        return next((zone for zone in system.zones if zone.index == self._index), None)

    @property
    def available(self) -> bool:
        """Return whether coordinator health and device state allow control."""
        system = self._system
        return (
            self.coordinator.last_update_success
            and system is not None
            and system.is_connected is not False
            and self._zone is not None
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the current room temperature."""
        zone = self._zone
        return None if zone is None else zone.temperature

    @property
    def current_humidity(self) -> int | None:
        """Return the current room humidity."""
        zone = self._zone
        return None if zone is None else zone.humidity

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode."""
        zone = self._zone
        if zone is None or zone.mode is None:
            return None
        return MODE_TO_HVAC.get(zone.mode.lower(), HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action derived from the reported stage."""
        zone = self._zone
        if zone is None or not zone.stage_status:
            return None
        stage = zone.stage_status.lower()
        if "cool" in stage:
            return HVACAction.COOLING
        if "heat" in stage:
            return HVACAction.HEATING
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def target_temperature(self) -> float | None:
        """Return the single target set point for heat or cool mode."""
        zone = self._zone
        if zone is None:
            return None
        if self.hvac_mode == HVACMode.HEAT:
            return zone.heat_set_point
        if self.hvac_mode == HVACMode.COOL:
            return zone.cool_set_point
        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return the cool set point when in heat/cool mode."""
        zone = self._zone
        if zone is None or self.hvac_mode != HVACMode.HEAT_COOL:
            return None
        return zone.cool_set_point

    @property
    def target_temperature_low(self) -> float | None:
        """Return the heat set point when in heat/cool mode."""
        zone = self._zone
        if zone is None or self.hvac_mode != HVACMode.HEAT_COOL:
            return None
        return zone.heat_set_point

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Update the zone's set points.

        Args:
            **kwargs: Home Assistant temperature arguments.
        """
        zone = self._zone
        if zone is None:
            raise HomeAssistantError("Entry-level zone unavailable, try again later")
        cool_set_point = zone.cool_set_point
        heat_set_point = zone.heat_set_point
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            if self.hvac_mode == HVACMode.HEAT:
                heat_set_point = temperature
            else:
                cool_set_point = temperature
        if (low := kwargs.get(ATTR_TARGET_TEMP_LOW)) is not None:
            heat_set_point = low
        if (high := kwargs.get(ATTR_TARGET_TEMP_HIGH)) is not None:
            cool_set_point = high
        await self.coordinator.async_perform_api_call(
            "set entry-level set points",
            partial(
                self.coordinator.api_connection.update_entry_level_zone,
                self._serial,
                self._index,
                cool_set_point=cool_set_point,
                heat_set_point=heat_set_point,
            ),
        )
        zone.cool_set_point = cool_set_point
        zone.heat_set_point = heat_set_point
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode.

        Args:
            hvac_mode: Requested Home Assistant HVAC mode.

        Raises:
            ValueError: Raised when the mode is unsupported.
        """
        mode = HVAC_TO_MODE.get(hvac_mode)
        if mode is None:
            raise ValueError(f"unsupported mode: {hvac_mode}")
        await self.coordinator.async_perform_api_call(
            "set entry-level mode",
            partial(
                self.coordinator.api_connection.update_entry_level_zone,
                self._serial,
                self._index,
                mode=mode,
            ),
        )
        zone = self._zone
        if zone is not None:
            zone.mode = mode
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the thermostat off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn the thermostat on (cool)."""
        await self.async_set_hvac_mode(HVACMode.COOL)
