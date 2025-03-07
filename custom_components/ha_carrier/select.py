"""Create select for heat source."""

from __future__ import annotations
from logging import Logger, getLogger

from carrier_api.const import HeatSourceTypes
from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    DATA_UPDATE_COORDINATOR,
    HEAT_SOURCE_ODU_ONLY_LABEL,
    HEAT_SOURCE_SYSTEM_LABEL,
    HEAT_SOURCE_IDU_ONLY_LABEL,
)
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

_LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create instances of binary sensors."""
    updater: CarrierDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_UPDATE_COORDINATOR]
    entities = []
    for system in updater.systems:
        entities.extend(
            [
                HeatSourceSelect(updater, system.profile.serial),
            ]
        )
    async_add_entities(entities)


class HeatSourceSelect(CarrierEntity, SelectEntity):
    """select for heat source."""

    _attr_icon = "mdi:heat-pump"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str):
        """Declare device class and identifiers."""
        super().__init__("Heat Source", updater, system_serial)
        if self.carrier_system.profile.outdoor_unit_type in ["hp2stg", "varcaphp", "multistghp"]:
            options = [self.idu_only_label(), HEAT_SOURCE_ODU_ONLY_LABEL, HEAT_SOURCE_SYSTEM_LABEL]
        else:
            options = [self.idu_only_label(), HEAT_SOURCE_SYSTEM_LABEL]
        self.entity_description = SelectEntityDescription(
            key=f"#{self.carrier_system.profile.serial}-heat_source",
            options=options
        )

    def idu_only_label(self) -> str | None:
        if self.carrier_system.profile.indoor_unit_source is None:
            return HEAT_SOURCE_IDU_ONLY_LABEL
        return HEAT_SOURCE_IDU_ONLY_LABEL.replace("gas", self.carrier_system.profile.indoor_unit_source)

    @property
    def current_option(self) -> str| None:
        """Return true if the binary sensor is on."""
        return {
            HeatSourceTypes.IDU_ONLY.value: self.idu_only_label(),
            HeatSourceTypes.ODU_ONLY.value: HEAT_SOURCE_ODU_ONLY_LABEL,
            HeatSourceTypes.SYSTEM.value: HEAT_SOURCE_SYSTEM_LABEL,
        }.get(self.carrier_system.config.heat_source, None)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        new_heat_source: HeatSourceTypes = {
            self.idu_only_label(): HeatSourceTypes.IDU_ONLY,
            HEAT_SOURCE_ODU_ONLY_LABEL: HeatSourceTypes.ODU_ONLY,
            HEAT_SOURCE_SYSTEM_LABEL: HeatSourceTypes.SYSTEM,
        }.get(option, HeatSourceTypes.SYSTEM)
        _LOGGER.debug(f"Selected heat source: {new_heat_source}")
        await self.coordinator.api_connection.set_heat_source(
            system_serial=self.carrier_system.profile.serial,
            heat_source=new_heat_source
        )

    @property
    def available(self) -> bool:
        """Return true if sensor is ready for display."""
        return self.current_option is not None
