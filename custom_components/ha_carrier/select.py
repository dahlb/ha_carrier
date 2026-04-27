"""Expose Carrier heat source selection as Home Assistant select entities."""

from __future__ import annotations

from functools import partial
import logging

from carrier_api import System
from carrier_api.const import HeatSourceTypes
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConfigEntryCarrier
from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity
from .const import HEAT_SOURCE_IDU_ONLY_LABEL, HEAT_SOURCE_ODU_ONLY_LABEL, HEAT_SOURCE_SYSTEM_LABEL

_LOGGER: logging.Logger = logging.getLogger(__name__)

HEAT_TYPES: list[str] = [
    "hp_heat",
    "electric_heat",
    "reheat",
    "loop_pump",
]


def has_heat(carrier_system: System) -> bool:
    """Return True if the Carrier system supports heat source selection."""
    return any(getattr(carrier_system.energy, heat_type, False) is True for heat_type in HEAT_TYPES)


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
    """
    coordinator = config_entry.runtime_data
    entities: list[SelectEntity] = [
        HeatSourceSelect(coordinator=coordinator, system_serial=system.profile.serial)
        for system in coordinator.systems
        if has_heat(system)
    ]
    async_add_entities(entities)


class CarrierSelect(CarrierEntity, SelectEntity):
    """Shared Carrier base class for select entities."""

    def __init__(
        self,
        entity_name: str,
        coordinator: CarrierDataUpdateCoordinator,
        system_serial: str | None = None,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize a Carrier select entity.

        Args:
            entity_name: Friendly suffix used in entity name and unique ID.
            coordinator: Coordinator that provides Carrier data.
            system_serial: Carrier system serial for this entity.
            unique_id_suffix: Optional stable suffix used for the entity unique ID.

        Raises:
            ValueError: Raised when no Carrier system serial is provided.
        """
        if system_serial is None:
            raise ValueError("Carrier select system serial is required")
        super().__init__(
            entity_name=entity_name,
            coordinator=coordinator,
            system_serial=system_serial,
            unique_id_suffix=unique_id_suffix,
        )
        self._sync_entity_attrs()

    def _update_entity_attrs(self) -> None:
        """Update select attrs from coordinator data."""
        self._attr_available = False


class HeatSourceSelect(CarrierSelect):
    """Select entity that controls which unit provides primary heating."""

    _attr_icon = "mdi:heat-pump"
    _attr_options: list[str]

    def __init__(self, coordinator: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize selectable heat source options for one system.

        Args:
            coordinator: Coordinator that provides Carrier system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__("Heat Source", coordinator, system_serial)
        if self.carrier_system.profile.outdoor_unit_type in [
            "hp2stg",
            "varcaphp",
            "multistghp",
            "GeoHP",
        ]:
            self._attr_options = [
                self._idu_only_label(self.carrier_system.profile.indoor_unit_source),
                HEAT_SOURCE_ODU_ONLY_LABEL,
                HEAT_SOURCE_SYSTEM_LABEL,
            ]
        else:
            self._attr_options = [
                self._idu_only_label(self.carrier_system.profile.indoor_unit_source),
                HEAT_SOURCE_SYSTEM_LABEL,
            ]

    @staticmethod
    def _idu_only_label(indoor_unit_source: str | None) -> str:
        """Return the label for indoor-unit-only heat source mode.

        Args:
            indoor_unit_source: Fuel source reported by the indoor unit.

        Returns:
            str: Human-readable option label.
        """
        if indoor_unit_source is None:
            return HEAT_SOURCE_IDU_ONLY_LABEL
        return HEAT_SOURCE_IDU_ONLY_LABEL.replace("gas", indoor_unit_source)

    def _update_entity_attrs(self) -> None:
        """Update heat source attrs from coordinator data."""
        self._attr_current_option = {
            HeatSourceTypes.IDU_ONLY.value: self._idu_only_label(
                self.carrier_system.profile.indoor_unit_source
            ),
            HeatSourceTypes.ODU_ONLY.value: HEAT_SOURCE_ODU_ONLY_LABEL,
            HeatSourceTypes.SYSTEM.value: HEAT_SOURCE_SYSTEM_LABEL,
        }.get(self.carrier_system.config.heat_source)
        self._attr_available = self._attr_current_option is not None

    async def async_select_option(self, option: str) -> None:
        """Apply a new heat source option to the Carrier system.

        Args:
            option: Selected Home Assistant option label.

        Raises:
            HomeAssistantError: Raised when the selected option is not a known heat source label.
        """
        heat_source_map: dict[str, HeatSourceTypes] = {
            self._idu_only_label(
                self.carrier_system.profile.indoor_unit_source
            ): HeatSourceTypes.IDU_ONLY,
            HEAT_SOURCE_ODU_ONLY_LABEL: HeatSourceTypes.ODU_ONLY,
            HEAT_SOURCE_SYSTEM_LABEL: HeatSourceTypes.SYSTEM,
        }
        if option not in heat_source_map:
            _LOGGER.error("Unsupported heat source option selected: %s", option)
            raise HomeAssistantError(f"Unsupported heat source option: {option}")

        new_heat_source = heat_source_map[option]
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
        self._write_local_state()
