"""Diagnostics payload builder for the Carrier integration."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .carrier_data_update_coordinator import CarrierDataUpdateCoordinator
from .const import (
    DATA_UPDATE_COORDINATOR,
    DOMAIN,
    TO_REDACT,
    TO_REDACT_DEVICE,
    TO_REDACT_ENTITIES,
    TO_REDACT_MAPPED,
    TO_REDACT_RAW,
)

LOGGER: Logger = getLogger(__package__)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, dict[str, Any]]:
    """Collect redacted integration diagnostics for a config entry.

    The diagnostics include config entry data, mapped system snapshots, raw
    Carrier payloads, and Home Assistant device/entity state linked to each
    Carrier serial.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry for which diagnostics were requested.

    Returns:
        dict[str, dict[str, Any]]: Redacted diagnostics keyed by section name
        and system serial.
    """
    updater: CarrierDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        DATA_UPDATE_COORDINATOR
    ]
    data = {
        "entry": async_redact_data(config_entry.as_dict(), TO_REDACT),
    }
    for carrier_system in updater.systems:
        system_data = {
            "mapped_data": async_redact_data(
                updater.mapped_system_data(carrier_system), TO_REDACT_MAPPED
            ),
            "profile_raw": async_redact_data(carrier_system.profile.raw, TO_REDACT_RAW),
            "status_raw": async_redact_data(carrier_system.status.raw, TO_REDACT_RAW),
            "config_raw": async_redact_data(carrier_system.config.raw, TO_REDACT_RAW),
            "energy_raw": async_redact_data(carrier_system.energy.raw, TO_REDACT_RAW),
        }
        data[carrier_system.profile.serial] = system_data

        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        hass_device = device_registry.async_get_device(
            identifiers={(DOMAIN, str(carrier_system.profile.serial))}
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
