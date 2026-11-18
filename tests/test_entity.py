"""Workflow tests for shared Carrier entity behavior."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
import yaml

from custom_components.ha_carrier.const import DOMAIN
from custom_components.ha_carrier.coordinator import CarrierDataUpdateCoordinator
from custom_components.ha_carrier.entity import CarrierZoneEntity

from .conftest import (
    FakeCarrierApiConnection,
    build_carrier_system,
    entity_for_unique_id,
    entity_id_for_unique_id,
)

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / DOMAIN
TRANSLATIONS: dict[str, Any] = json.loads(
    (INTEGRATION_DIR / "translations" / "en.json").read_text(encoding="utf-8")
)


def test_zone_entity_resolves_zone_name_from_coordinator_systems() -> None:
    """Resolve a zone display name from coordinator system data."""
    system = build_carrier_system(zone_name="Bedroom", zone_id="2")
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [system]

    assert CarrierZoneEntity.resolve_zone_name(coordinator, "ABC123", "2") == "Bedroom"


def test_zone_entity_raises_when_zone_cannot_be_resolved() -> None:
    """Raise a clear error when a zone ID is missing from coordinator data."""
    system = build_carrier_system(zone_name="Bedroom", zone_id="2")
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = [system]

    with pytest.raises(ValueError, match="Config Zone not found: missing"):
        CarrierZoneEntity.resolve_zone_name(coordinator, "ABC123", "missing")


@pytest.mark.asyncio
async def test_every_named_entity_uses_a_defined_translation_key(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Give every non-climate entity a translation key that exists in en.json."""
    await setup_integration()

    entries = [entry for entry in er.async_get(hass).entities.values() if entry.platform == DOMAIN]
    assert entries
    for entry in entries:
        if entry.domain == "climate":
            # Zone thermostats are named after the user's zone, not a fixed string.
            assert entry.translation_key is None
            continue
        assert entry.translation_key in TRANSLATIONS["entity"][entry.domain], entry.unique_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "unique_id", "expected_name"),
    [
        ("binary_sensor", "abc123_online", "Home Online"),
        ("binary_sensor", "abc123_zone_1_occupancy", "Home Living Room Occupancy"),
        ("sensor", "abc123_zone_1_temperature", "Home Living Room Temperature"),
        ("sensor", "abc123_gas_usage_year_to_date", "Home Gas Usage Year to Date"),
        ("sensor", "abc123_websocket_last_updated", "Home Websocket Last Updated"),
        ("sensor", "abc123_odu_var", "Home ODU Var"),
        ("select", "abc123_heat_source", "Home Heat Source"),
        ("climate", "abc123_zone_1_thermostat", "Home Living Room"),
    ],
)
async def test_translated_entity_names_keep_existing_friendly_names(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
    domain: str,
    unique_id: str,
    expected_name: str,
) -> None:
    """Resolve translated names, including placeholders, to the previous names."""
    await setup_integration()

    state = hass.states.get(entity_id_for_unique_id(hass, domain, unique_id))

    assert state is not None
    assert state.attributes["friendly_name"] == expected_name


def test_every_raised_exception_translation_key_is_defined() -> None:
    """Define a message in en.json for every exception translation key in the code."""
    raised_keys: set[str] = set()
    for source in INTEGRATION_DIR.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        raised_keys.update(
            re.findall(r'translation_domain=DOMAIN,\s*translation_key="([a-z_]+)"', text)
        )

    assert raised_keys
    assert raised_keys == set(TRANSLATIONS["exceptions"])


def test_every_service_and_field_is_translated() -> None:
    """Translate every service and service field declared in services.yaml."""
    services = yaml.safe_load((INTEGRATION_DIR / "services.yaml").read_text(encoding="utf-8"))

    assert set(services) == set(TRANSLATIONS["services"])
    for service, definition in services.items():
        translated = TRANSLATIONS["services"][service]
        assert translated["name"]
        assert translated["description"]
        assert set(definition.get("fields", {})) == set(translated.get("fields", {}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unique_id", "break_data"),
    [
        # Zone missing from status: _status_zone raises.
        ("abc123_zone_2_temperature", lambda systems: systems[0].status.zones.pop()),
        # Zone missing from config: _config_zone raises.
        ("abc123_zone_2_thermostat", lambda systems: systems[0].config.zones.pop()),
        # System dropped from the account: carrier_system raises.
        ("abc123_outdoor_temperature", lambda systems: systems.clear()),
    ],
)
async def test_entities_go_unavailable_when_their_data_disappears(
    hass: HomeAssistant,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Any],
    unique_id: str,
    break_data: Callable[[list[Any]], Any],
) -> None:
    """Turn a lookup error while reading Carrier data into an unavailable entity."""
    carrier_api.systems = [build_carrier_system(second_zone_id="2")]
    entry = await setup_integration()
    domain = "climate" if unique_id.endswith("thermostat") else "sensor"
    entity_id = entity_id_for_unique_id(hass, domain, unique_id)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    break_data(entry.runtime_data.systems)
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_zone_lookups_require_a_zone_id(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Raise a clear error when a zone entity has no zone API ID."""
    await setup_integration()
    entity = entity_for_unique_id(hass, "sensor", "abc123_zone_1_temperature")
    entity.zone_api_id = None

    with pytest.raises(ValueError, match="No zone api id defined"):
        _ = entity._status_zone
    with pytest.raises(ValueError, match="No zone api id defined"):
        _ = entity._config_zone


def test_zone_name_resolution_requires_a_known_system() -> None:
    """Raise a clear error when resolving a zone name for an unknown system."""
    coordinator = CarrierDataUpdateCoordinator.__new__(CarrierDataUpdateCoordinator)
    coordinator.systems = []

    with pytest.raises(ValueError, match="Carrier System not found: ABC123"):
        CarrierZoneEntity.resolve_zone_name(coordinator, "ABC123", "1")


@pytest.mark.asyncio
async def test_zone_entity_unique_id_without_a_suffix(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Key a zone entity by its zone alone when no suffix is given."""
    entry = await setup_integration()

    entity = CarrierZoneEntity("", entry.runtime_data, "ABC123", "1")

    assert entity.unique_id == "abc123_zone_1"
