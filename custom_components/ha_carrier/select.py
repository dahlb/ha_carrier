"""Create select for heat source."""

from __future__ import annotations
from logging import Logger, getLogger
import asyncio

from carrier_api.const import HeatSourceTypes
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    DATA_SYSTEMS,
    HEAT_SOURCE_ODU_ONLY_LABEL,
    HEAT_SOURCE_SYSTEM_LABEL,
    HEAT_SOURCE_IDU_ONLY_LABEL,
)
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Create instances of binary sensors."""
    updaters: list[CarrierDataUpdateCoordinator] = hass.data[DOMAIN][
        config_entry.entry_id
    ][DATA_SYSTEMS]
    entities = []
    for updater in updaters:
        entities.extend(
            [
                HeatSourceSelect(updater),
            ]
        )
    async_add_entities(entities)


class HeatSourceSelect(CarrierEntity, SelectEntity):
    """select for heat source."""

    _attr_icon = "mdi:heat-pump"

    def __init__(self, updater):
        """Declare device class and identifiers."""
        super().__init__("Heat Source", updater)
        if updater.carrier_system.profile.outdoor_unit_type == "varcaphp":
            options = [self.idu_only_label(), HEAT_SOURCE_ODU_ONLY_LABEL, HEAT_SOURCE_SYSTEM_LABEL]
        else:
            options = [self.idu_only_label(), HEAT_SOURCE_SYSTEM_LABEL]
        self.entity_description = SelectEntityDescription(
            key=f"#{updater.carrier_system.serial}-heat_source",
            options=options
        )

    def idu_only_label(self) -> str | None:
        return HEAT_SOURCE_IDU_ONLY_LABEL.replace("gas", self._updater.carrier_system.profile.indoor_unit_source)

    @property
    def current_option(self) -> str| None:
        """Return true if the binary sensor is on."""
        return {
            HeatSourceTypes.IDU_ONLY.value: self.idu_only_label(),
            HeatSourceTypes.ODU_ONLY.value: HEAT_SOURCE_ODU_ONLY_LABEL,
            HeatSourceTypes.SYSTEM.value: HEAT_SOURCE_SYSTEM_LABEL,
        }.get(self._updater.carrier_system.config.heat_source, None)

    def select_option(self, option: str) -> None:
        """Change the selected option."""
        new_heat_source: HeatSourceTypes = {
            self.idu_only_label(): HeatSourceTypes.IDU_ONLY,
            HEAT_SOURCE_ODU_ONLY_LABEL: HeatSourceTypes.ODU_ONLY,
            HEAT_SOURCE_SYSTEM_LABEL: HeatSourceTypes.SYSTEM,
        }.get(option, HeatSourceTypes.SYSTEM)
        LOGGER.debug(f"Selected heat source: {new_heat_source}")
        self._updater.api_connection.set_heat_source(
            system_serial=self._updater.carrier_system.serial,
            heat_source=new_heat_source
        )
        asyncio.run_coroutine_threadsafe(asyncio.sleep(5), self.hass.loop).result()
        asyncio.run_coroutine_threadsafe(
            self._updater.async_request_refresh(), self.hass.loop
        ).result()
