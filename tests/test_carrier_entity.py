"""Workflow tests for shared Carrier entity behavior."""

from __future__ import annotations

import pytest

from custom_components.ha_carrier.carrier_data_update_coordinator import (
    CarrierDataUpdateCoordinator,
)
from custom_components.ha_carrier.carrier_entity import CarrierZoneEntity

from .conftest import build_carrier_system


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
