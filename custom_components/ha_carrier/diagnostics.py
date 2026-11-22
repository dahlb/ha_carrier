"""Diagnostics payload builder for the Carrier integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import ConfigEntryCarrier
from .const import (
    DOMAIN,
    TO_REDACT,
    TO_REDACT_DEVICE,
    TO_REDACT_ENTITIES,
    TO_REDACT_ENTRY_LEVEL,
    TO_REDACT_MAPPED,
    TO_REDACT_RAW,
)
from .coordinator import CarrierDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


def _isoformat(timestamp: datetime | None) -> str | None:
    """Return a timestamp as ISO 8601 text, or None when it is unset.

    Args:
        timestamp: Timestamp to format.

    Returns:
        str | None: ISO 8601 text, or None.
    """
    return timestamp.isoformat() if timestamp is not None else None


def _coordinator_diagnostics(coordinator: CarrierDataUpdateCoordinator) -> dict[str, Any]:
    """Describe the coordinator's refresh, websocket, and outage-tracking state.

    Args:
        coordinator: Coordinator for the config entry.

    Returns:
        dict[str, Any]: Refresh health, schedule, timestamps, websocket task
            status, and resiliency counters.
    """
    last_exception = coordinator.last_exception
    websocket_task = coordinator.websocket_task
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": type(last_exception).__name__ if last_exception else None,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None
        ),
        "full_refresh_interval_seconds": coordinator.full_refresh_interval.total_seconds(),
        "data_flush": coordinator.data_flush,
        "timestamp_all_data": _isoformat(coordinator.timestamp_all_data),
        "timestamp_websocket": _isoformat(coordinator.timestamp_websocket),
        "timestamp_energy": _isoformat(coordinator.timestamp_energy),
        "websocket_task_running": websocket_task is not None and not websocket_task.done(),
        "resiliency": {
            "consecutive_unauthorized": coordinator.resiliency.consecutive_unauthorized,
            "consecutive_transient": coordinator.resiliency.consecutive_transient,
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntryCarrier
) -> dict[str, dict[str, Any]]:
    """Collect redacted integration diagnostics for a config entry.

    The diagnostics include config entry data, coordinator health, mapped
    system snapshots, raw Carrier payloads, entry-level (Smart Thermostat)
    payloads, and Home Assistant device/entity state for each Carrier system.
    No section key or value contains a system serial.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry for which diagnostics were requested.

    Returns:
        dict[str, dict[str, Any]]: Redacted diagnostics keyed by section name,
        with one ``system_<n>`` section per system in coordinator order.
    """
    updater = config_entry.runtime_data
    data = {
        "entry": async_redact_data(config_entry.as_dict(), TO_REDACT),
        "coordinator": _coordinator_diagnostics(updater),
        "entry_level_systems": {
            "loaded": updater.entry_level_systems_loaded,
            "systems": [
                async_redact_data(system.raw, TO_REDACT_ENTRY_LEVEL)
                for system in updater.entry_level_systems
            ],
        },
    }
    # Systems are keyed by position, not serial, so the serial stays redacted.
    for index, carrier_system in enumerate(updater.systems, start=1):
        system_data = {
            "mapped_data": async_redact_data(
                updater.mapped_system_data(carrier_system), TO_REDACT_MAPPED
            ),
            "profile_raw": async_redact_data(carrier_system.profile.raw, TO_REDACT_RAW),
            "status_raw": async_redact_data(carrier_system.status.raw, TO_REDACT_RAW),
            "config_raw": async_redact_data(carrier_system.config.raw, TO_REDACT_RAW),
            "energy_raw": async_redact_data(carrier_system.energy.raw, TO_REDACT_RAW),
        }
        data[f"system_{index}"] = system_data

        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        hass_device = device_registry.async_get_device_by_identifier(
            (DOMAIN, str(carrier_system.profile.serial)), config_entry.entry_id
        )
        if hass_device is not None:
            system_data["device"] = {
                **async_redact_data(hass_device.dict_repr, TO_REDACT_DEVICE),
                "entities": {},
            }

            hass_entities = er.async_entries_for_device(
                entity_registry,
                device_id=hass_device.id,
                include_disabled_entities=True,
            )

            for entity_entry in hass_entities:
                state = hass.states.get(entity_entry.entity_id)
                state_dict = None
                entity_data = dict(entity_entry.as_partial_dict)
                entity_data.pop("entity_id", None)
                if state:
                    state_dict = dict(state.as_dict())
                    # The entity_id is already provided at root level.
                    state_dict.pop("entity_id", None)
                    # The context doesn't provide useful information in this case.
                    state_dict.pop("context", None)

                system_data["device"]["entities"][entity_entry.entity_id] = {
                    **async_redact_data(entity_data, TO_REDACT_ENTITIES),
                    "state": state_dict,
                }

    return data
