"""Expose Carrier heat source selection as Home Assistant select entities."""

from __future__ import annotations

from functools import partial
import logging

from carrier_api.const import HeatSourceTypes
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConfigEntryCarrier
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity
from .const import HEAT_SOURCE_IDU_ONLY_LABEL, HEAT_SOURCE_ODU_ONLY_LABEL, HEAT_SOURCE_SYSTEM_LABEL

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntryCarrier,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register heat source select entities for each system.

    Args:
        hass: Home Assistant instance.
        config_entry: Carrier integration config entry.
        async_add_entities: Callback used to register entity instances.

    Returns:
        None: Entities are registered through the callback.
    """
    updater = config_entry.runtime_data
    entities = []
    for system in updater.systems:
        entities.extend(
            [
                HeatSourceSelect(updater, system.profile.serial),
            ]
        )
    async_add_entities(entities)


class HeatSourceSelect(CarrierEntity, SelectEntity):
    """Select entity that controls which unit provides primary heating."""

    _attr_icon = "mdi:heat-pump"

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize selectable heat source options for one system.

        Args:
            updater: Coordinator that provides Carrier system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__("Heat Source", updater, system_serial)
        if self.carrier_system.profile.outdoor_unit_type in [
            "hp2stg",
            "varcaphp",
            "multistghp",
            "GeoHP",
        ]:
            options = [
                self.idu_only_label(),
                HEAT_SOURCE_ODU_ONLY_LABEL,
                HEAT_SOURCE_SYSTEM_LABEL,
            ]
        else:
            options = [self.idu_only_label(), HEAT_SOURCE_SYSTEM_LABEL]
        self.entity_description = SelectEntityDescription(
            key=f"#{self.carrier_system.profile.serial}-heat_source", options=options
        )

    def idu_only_label(self) -> str:
        """Return the label for indoor-unit-only heat source mode.

        Returns:
            str: Human-readable option label with the indoor fuel source when available.
        """
        if self.carrier_system.profile.indoor_unit_source is None:
            return HEAT_SOURCE_IDU_ONLY_LABEL
        return HEAT_SOURCE_IDU_ONLY_LABEL.replace(
            "gas", self.carrier_system.profile.indoor_unit_source
        )

    @property
    def current_option(self) -> str | None:
        """Return the currently selected heat source option label.

        Returns:
            str | None: Matching option label for the active Carrier heat source.
        """
        return {
            HeatSourceTypes.IDU_ONLY.value: self.idu_only_label(),
            HeatSourceTypes.ODU_ONLY.value: HEAT_SOURCE_ODU_ONLY_LABEL,
            HeatSourceTypes.SYSTEM.value: HEAT_SOURCE_SYSTEM_LABEL,
        }.get(self.carrier_system.config.heat_source)

    async def async_select_option(self, option: str) -> None:
        """Apply a new heat source option to the Carrier system.

        Args:
            option: Selected Home Assistant option label.

        Returns:
            None: The state is updated in-place and written to Home Assistant.
        """
        new_heat_source: HeatSourceTypes = {
            self.idu_only_label(): HeatSourceTypes.IDU_ONLY,
            HEAT_SOURCE_ODU_ONLY_LABEL: HeatSourceTypes.ODU_ONLY,
            HEAT_SOURCE_SYSTEM_LABEL: HeatSourceTypes.SYSTEM,
        }.get(option, HeatSourceTypes.SYSTEM)
        _LOGGER.debug("Selected heat source: %s", new_heat_source)
        await self.coordinator.async_perform_api_call(
            "set heat source",
            partial(
                self.coordinator.api_connection.set_heat_source,
                system_serial=self.carrier_system.profile.serial,
                heat_source=new_heat_source,
            ),
        )
        self.carrier_system.config.heat_source = new_heat_source.value
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicate whether the select can present a valid option.

        Returns:
            bool: True when a current option can be resolved.
        """
        return self.current_option is not None
