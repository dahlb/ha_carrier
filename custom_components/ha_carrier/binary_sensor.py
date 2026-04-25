"""Expose Carrier binary sensor entities for connectivity and runtime state."""

from __future__ import annotations

from logging import Logger, getLogger

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .carrier_entity import CarrierEntity

_LOGGER: Logger = getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register Carrier binary sensor entities for one config entry.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry that owns the Carrier account.
        async_add_entities: Callback used by Home Assistant to register entities.

    Returns:
        None: Entities are added through the callback.
    """
    updater: CarrierDataUpdateCoordinator = config_entry.runtime_data
    entities = []
    for carrier_system in updater.systems:
        entities.extend(
            [
                OnlineSensor(updater, carrier_system.profile.serial),
                HumidifierSensor(updater, carrier_system.profile.serial),
            ]
        )
        for zone in carrier_system.config.zones:
            if zone.occupancy_enabled:
                entities.extend(
                    [
                        OccupancySensor(
                            updater, carrier_system.profile.serial, zone_api_id=zone.api_id
                        ),
                    ]
                )
    async_add_entities(entities)


class OnlineSensor(CarrierEntity, BinarySensorEntity):
    """Binary sensor that reports whether the Carrier system is reachable."""

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize connectivity metadata for a Carrier system.

        Args:
            updater: Coordinator that provides system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__("Online", updater, system_serial)
        self.entity_description = BinarySensorEntityDescription(
            key=f"#{self.carrier_system.profile.serial}-online",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            icon="mdi:wifi-check",
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the thermostat currently reports as online.

        Returns:
            bool | None: True when connected, False when disconnected.
        """
        return not self.carrier_system.status.is_disconnected

    @property
    def icon(self) -> str | None:
        """Return an icon that reflects current connectivity.

        Returns:
            str | None: Connected icon when online, fallback wifi icon otherwise.
        """
        if self.is_on:
            return self.entity_description.icon
        return "mdi:wifi-strength-outline"

    @property
    def available(self) -> bool:
        """Indicate whether the sensor has a usable boolean state.

        Returns:
            bool: True when state is available.
        """
        return self.is_on is not None


class OccupancySensor(CarrierEntity, BinarySensorEntity):
    """Binary sensor that mirrors occupancy detection for a Carrier zone."""

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(
        self, updater: CarrierDataUpdateCoordinator, system_serial: str, zone_api_id: str
    ) -> None:
        """Initialize an occupancy entity tied to one zone.

        Args:
            updater: Coordinator that provides system and zone state.
            system_serial: Unique Carrier system serial number.
            zone_api_id: API identifier for the target zone.
        """
        self.zone_api_id: str = zone_api_id
        self.coordinator = updater
        self.coordinator_context = system_serial
        super().__init__(f"{self._config_zone.name} Occupancy", updater, system_serial)

    @property
    def is_on(self) -> bool | None:
        """Return whether the zone currently reports occupancy.

        Returns:
            bool | None: True when occupancy is detected.
        """
        return self._status_zone.occupancy

    @property
    def available(self) -> bool:
        """Indicate whether occupancy state can be shown.

        Returns:
            bool: True when zone occupancy data is available.
        """
        return self.is_on is not None


class HumidifierSensor(CarrierEntity, BinarySensorEntity):
    """Binary sensor that indicates whether humidification is active."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, updater: CarrierDataUpdateCoordinator, system_serial: str) -> None:
        """Initialize a humidifier runtime sensor for one Carrier system.

        Args:
            updater: Coordinator that provides system state.
            system_serial: Unique Carrier system serial number.
        """
        super().__init__("Humidifier Running", updater, system_serial)

    @property
    def is_on(self) -> bool | None:
        """Return whether the humidifier is currently running.

        Returns:
            bool | None: Runtime status reported by the Carrier API.
        """
        return self.carrier_system.status.humidifier_on

    @property
    def icon(self) -> str | None:
        """Return an icon that reflects humidifier runtime state.

        Returns:
            str | None: On/off humidifier icon based on current state.
        """
        if self.is_on:
            return "mdi:air-humidifier"
        return "mdi:air-humidifier-off"

    @property
    def available(self) -> bool:
        """Indicate whether humidifier state can be displayed.

        Returns:
            bool: True when humidifier state is available.
        """
        return self.is_on is not None
