"""Shared pytest fixtures for Carrier Home Assistant workflow tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from carrier_api import Config, Energy, Profile, Status, System
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components
from custom_components.ha_carrier.const import DOMAIN

USERNAME = "user@example.com"
PASSWORD = "password"
IDENTITY_ID = "identity-123"


class FakeCarrierWebsocket:
    """Small websocket fake that records callbacks and blocks until cancelled."""

    def __init__(self) -> None:
        """Initialize websocket callback storage."""
        self.callbacks: list[Callable[[str], Any]] = []
        self.listener_errors: list[BaseException] = []
        self.listener_calls = 0

    def callback_add(self, callback: Callable[[str], Any]) -> None:
        """Store a websocket callback registered by the coordinator.

        Args:
            callback: Callback provided by the integration.
        """
        self.callbacks.append(callback)

    async def listener(self) -> None:
        """Block until Home Assistant cancels the websocket task."""
        self.listener_calls += 1
        if self.listener_errors:
            raise self.listener_errors.pop(0)
        await asyncio.Event().wait()


class FakeCarrierApiConnection:
    """Carrier API fake used as the only external boundary in tests."""

    def __init__(
        self,
        *,
        username: str = USERNAME,
        password: str = PASSWORD,
        identity_id: str = IDENTITY_ID,
        systems: list[System] | None = None,
    ) -> None:
        """Initialize the fake API connection.

        Args:
            username: Carrier account username.
            password: Carrier account password.
            identity_id: Carrier account identity ID returned by user info.
            systems: Systems returned by ``load_data``.
        """
        self.username = username
        self.password = password
        self.identity_id = identity_id
        self.systems = systems or [build_carrier_system()]
        self.api_websocket = FakeCarrierWebsocket()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.load_data_error: BaseException | None = None
        self.cleanup_calls = 0

    async def load_data(self) -> list[System]:
        """Return configured systems or raise a configured load error."""
        self.calls.append(("load_data", {}))
        if self.load_data_error is not None:
            raise self.load_data_error
        return self.systems

    async def cleanup(self) -> None:
        """Record credential-validation cleanup."""
        self.cleanup_calls += 1

    async def get_user_info(self) -> dict[str, Any]:
        """Return the fake Carrier account identity payload."""
        self.calls.append(("get_user_info", {}))
        return {"user": {"identityId": self.identity_id}}

    async def get_energy(self, system_serial: str) -> dict[str, Any]:
        """Return current energy payload for a Carrier system.

        Args:
            system_serial: Carrier system serial requested by the coordinator.

        Returns:
            dict[str, Any]: Carrier GraphQL-shaped energy response.
        """
        self.calls.append(("get_energy", {"system_serial": system_serial}))
        system = self._system(system_serial)
        return {"infinityEnergy": deepcopy(system.energy.raw)}

    async def set_config_mode(self, *, system_serial: str, mode: Any) -> None:
        """Record a system mode write."""
        self.calls.append(("set_config_mode", {"system_serial": system_serial, "mode": mode}))

    async def set_config_heat_humidity(self, *, system_serial: str, humidity_target: int) -> None:
        """Record a target humidity write."""
        self.calls.append(
            (
                "set_config_heat_humidity",
                {"system_serial": system_serial, "humidity_target": humidity_target},
            )
        )

    async def resume_schedule(self, *, system_serial: str, zone_id: str) -> None:
        """Record a resume-schedule write."""
        self.calls.append(("resume_schedule", {"system_serial": system_serial, "zone_id": zone_id}))

    async def set_config_hold(
        self,
        *,
        system_serial: str,
        zone_id: str,
        activity_type: Any,
        hold_until: Any,
    ) -> None:
        """Record an activity hold write."""
        self.calls.append(
            (
                "set_config_hold",
                {
                    "system_serial": system_serial,
                    "zone_id": zone_id,
                    "activity_type": activity_type,
                    "hold_until": hold_until,
                },
            )
        )

    async def update_fan(
        self,
        *,
        system_serial: str,
        zone_id: str,
        activity_type: Any,
        fan_mode: Any,
    ) -> None:
        """Record an activity fan-mode write."""
        self.calls.append(
            (
                "update_fan",
                {
                    "system_serial": system_serial,
                    "zone_id": zone_id,
                    "activity_type": activity_type,
                    "fan_mode": fan_mode,
                },
            )
        )

    async def set_config_manual_activity(
        self,
        *,
        system_serial: str,
        zone_id: str,
        heat_set_point: str,
        cool_set_point: str,
        fan_mode: Any,
    ) -> None:
        """Record a manual-activity write."""
        self.calls.append(
            (
                "set_config_manual_activity",
                {
                    "system_serial": system_serial,
                    "zone_id": zone_id,
                    "heat_set_point": heat_set_point,
                    "cool_set_point": cool_set_point,
                    "fan_mode": fan_mode,
                },
            )
        )

    async def set_heat_source(self, *, system_serial: str, heat_source: Any) -> None:
        """Record a heat-source write."""
        self.calls.append(
            ("set_heat_source", {"system_serial": system_serial, "heat_source": heat_source})
        )

    def _system(self, system_serial: str) -> System:
        """Return one fake Carrier system by serial.

        Args:
            system_serial: Serial to locate.

        Returns:
            System: Matching Carrier system.

        Raises:
            ValueError: Raised when the serial is not part of the fake account.
        """
        for system in self.systems:
            if system.profile.serial == system_serial:
                return system
        raise ValueError(f"Unknown fake Carrier system: {system_serial}")


def _schedule_day() -> dict[str, Any]:
    """Return one enabled Carrier schedule day."""
    return {
        "period": [
            {"enabled": "on", "time": "00:00", "activity": "home"},
            {"enabled": "on", "time": "23:59", "activity": "sleep"},
        ]
    }


def _zone_raw(*, zone_id: str, name: str, occupancy_enabled: bool = True) -> dict[str, Any]:
    """Return Carrier config-zone JSON for tests."""
    return {
        "id": zone_id,
        "name": name,
        "enabled": "on",
        "holdActivity": "home",
        "hold": "off",
        "otmr": "",
        "occEnabled": "on" if occupancy_enabled else "off",
        "activities": [
            {"type": "home", "id": "home", "fan": "off", "htsp": 68, "clsp": 74},
            {"type": "away", "id": "away", "fan": "off", "htsp": 62, "clsp": 82},
            {"type": "sleep", "id": "sleep", "fan": "off", "htsp": 66, "clsp": 76},
            {"type": "manual", "id": "manual", "fan": "low", "htsp": 69, "clsp": 75},
        ],
        "program": {"day": [_schedule_day() for _ in range(7)]},
    }


def _status_zone_raw(*, zone_id: str, name: str, occupancy: bool = True) -> dict[str, Any]:
    """Return Carrier status-zone JSON for tests."""
    return {
        "id": zone_id,
        "name": name,
        "enabled": "on",
        "currentActivity": "home",
        "rt": 70.0,
        "rh": 45,
        "occupancy": "occupied" if occupancy else "unoccupied",
        "fan": "off",
        "hold": "off",
        "otmr": "",
        "htsp": 68,
        "clsp": 74,
        "zoneconditioning": "idle",
        "damperposition": 50,
    }


def build_carrier_system(
    *,
    serial: str = "ABC123",
    name: str = "Home",
    zone_name: str = "Living Room",
    zone_id: str = "1",
    has_heat_pump: bool = True,
    fan_enabled: bool | None = None,
    disconnected: bool = False,
) -> System:
    """Build a realistic Carrier system from ``carrier_api`` model classes.

    Args:
        serial: System serial number. Defaults to ``"ABC123"``.
        name: Human-readable system name. Defaults to ``"Home"``.
        zone_name: Display name for the single zone. Defaults to ``"Living Room"``.
        zone_id: Identifier assigned to the zone. Defaults to ``"1"``.
        has_heat_pump: When ``True``, configure the outdoor unit as a variable-capacity
            heat pump; otherwise configure it as an air conditioner. Defaults to ``True``.
        fan_enabled: Optional Carrier ``cfgfan`` value. Defaults to ``None`` to omit the
            field and exercise energy-based fan capability fallback.
        disconnected: When ``True``, mark the status payload as disconnected so tests can
            exercise offline behavior. Defaults to ``False``.

    Returns:
        System: A ``carrier_api`` ``System`` instance populated with realistic profile,
        config, status, and energy payloads.
    """
    profile = Profile(
        {
            "name": name,
            "serial": serial,
            "model": "Infinity",
            "brand": "Carrier",
            "firmware": "1.0",
            "indoorModel": "FE4",
            "indoorSerial": "IDU123",
            "idutype": "furnace",
            "idusource": "gas",
            "outdoorModel": "25VNA",
            "outdoorSerial": "ODU123",
            "odutype": "varcaphp" if has_heat_pump else "ac",
        }
    )
    config_raw = {
        "cfgem": "F",
        "mode": "auto",
        "heatsource": "system",
        "etag": "etag",
        "fueltype": "gas",
        "gasunit": "therm",
        "cfguv": "on",
        "cfghumid": "on",
        "humidityHome": {"rhtg": 7},
        "vacmaxt": 82,
        "vacmint": 60,
        "vacfan": "off",
        "zones": [_zone_raw(zone_id=zone_id, name=zone_name)],
    }
    if fan_enabled is not None:
        config_raw["cfgfan"] = "on" if fan_enabled else "off"
    config = Config(config_raw)
    status = Status(
        {
            "oat": 40,
            "mode": "gasheat",
            "cfgem": "F",
            "filtrlvl": 20,
            "humlvl": 30,
            "humid": "on",
            "uvlvl": 10,
            "isDisconnected": disconnected,
            "idu": {"cfm": 1200, "blwrpm": 500, "statpress": 0.2, "opstat": "idle"},
            "odu": {"opstat": "idle"},
            "utcTime": "2026-05-05T12:00:00+00:00",
            "zones": [_status_zone_raw(zone_id=zone_id, name=zone_name)],
        }
    )
    energy = Energy(
        {
            "energyConfig": {
                "seer": 18,
                "hspf": 10,
                "cooling": {"display": True, "enabled": True},
                "hpheat": {"display": True, "enabled": True},
                "fan": {"display": True, "enabled": True},
                "eheat": {"display": False, "enabled": False},
                "reheat": {"display": False, "enabled": False},
                "fangas": {"display": False, "enabled": False},
                "gas": {"display": True, "enabled": True},
                "looppump": {"display": False, "enabled": False},
            },
            "energyPeriods": [
                {
                    "energyPeriodType": "year1",
                    "coolingKwh": 100,
                    "hPHeatKwh": 80,
                    "fanKwh": 12,
                    "gasKwh": 300,
                },
                {
                    "energyPeriodType": "day1",
                    "coolingKwh": 1,
                    "hPHeatKwh": 2,
                    "fanKwh": 3,
                    "gasKwh": 4,
                },
                {
                    "energyPeriodType": "month1",
                    "coolingKwh": 10,
                    "hPHeatKwh": 20,
                    "fanKwh": 30,
                    "gasKwh": 40,
                },
            ],
        }
    )
    return System(profile=profile, status=status, config=config, energy=energy)


def entity_id_for_unique_id(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    """Return the entity ID registered for an integration unique ID.

    Args:
        hass: Home Assistant instance.
        domain: Entity platform domain.
        unique_id: Integration unique ID to resolve.

    Returns:
        str: Registered entity ID.

    Raises:
        AssertionError: Raised when the unique ID was not registered.
    """
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enable loading custom integrations in Home Assistant pytest."""
    components_path = str(Path(__file__).parents[1] / "custom_components")
    monkeypatch.setattr(custom_components, "__path__", [components_path])


@pytest.fixture
def carrier_api() -> FakeCarrierApiConnection:
    """Return the default fake Carrier API connection."""
    return FakeCarrierApiConnection()


@pytest.fixture
def patch_carrier_api(
    carrier_api: FakeCarrierApiConnection,
) -> Iterator[FakeCarrierApiConnection]:
    """Patch Carrier API constructors across integration modules.

    Args:
        carrier_api: Fake connection to return from every constructor.

    Yields:
        FakeCarrierApiConnection: The patched fake API object.
    """

    def build_connection(*, username: str, password: str) -> FakeCarrierApiConnection:
        """Return the test fake while recording supplied credentials."""
        carrier_api.username = username
        carrier_api.password = password
        return carrier_api

    with (
        patch("custom_components.ha_carrier.ApiConnectionGraphql", build_connection),
        patch("custom_components.ha_carrier.config_flow.ApiConnectionGraphql", build_connection),
        patch("custom_components.ha_carrier.migrate.ApiConnectionGraphql", build_connection),
    ):
        yield carrier_api


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> AsyncIterator[Callable[..., Any]]:
    """Return a helper that sets up a Carrier config entry through Home Assistant."""
    entries: list[ConfigEntry] = []

    async def _setup(
        *,
        options: dict[str, Any] | None = None,
        version: int = 2,
    ) -> ConfigEntry:
        """Set up one Carrier config entry.

        Args:
            options: Config entry options.
            version: Config entry version.

        Returns:
            ConfigEntry: Config entry loaded by Home Assistant.
        """
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title=USERNAME,
            unique_id=USERNAME,
            data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
            options=options or {},
            version=version,
        )
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        entries.append(config_entry)
        return config_entry

    yield _setup

    for entry in entries:
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
