"""Tests for scheduled websocket reconciles (background tick + post-write burst)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from unittest.mock import patch

from carrier_api import CarrierApiWebsocketError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_carrier.const import (
    DOMAIN,
    RECONCILE_BACKGROUND_INTERVAL_SECONDS,
    RECONCILE_BURST_DELAYS_SECONDS,
)

from .conftest import PASSWORD, USERNAME, FakeCarrierApiConnection


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Advance frozen time and run any Home Assistant timers that came due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _noop_write() -> None:
    """Stand in for a Carrier write request."""


@pytest.mark.asyncio
async def test_background_tick_sends_reconcile_periodically(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Awaitable[ConfigEntry]],
) -> None:
    """Send one reconcile frame per background interval while set up."""
    await setup_integration()
    websocket = carrier_api.api_websocket
    websocket.reconcile_calls = 0

    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS)
    assert websocket.reconcile_calls == 1

    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS)
    assert websocket.reconcile_calls == 2


@pytest.mark.asyncio
async def test_write_triggers_exponential_burst(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Awaitable[ConfigEntry]],
) -> None:
    """Follow a write with reconciles at each exponential backoff delay."""
    config_entry = await setup_integration()
    coordinator = config_entry.runtime_data
    websocket = carrier_api.api_websocket
    websocket.reconcile_calls = 0

    await coordinator.async_perform_api_call("test write", _noop_write)
    await hass.async_block_till_done()
    assert websocket.reconcile_calls == 0

    # Steps through 128 s sit at cumulative 254 s, before the first 300 s
    # background tick, so each advance observes exactly one burst send.
    expected = 0
    for delay in RECONCILE_BURST_DELAYS_SECONDS[:-1]:
        await _advance(hass, freezer, delay)
        expected += 1
        assert websocket.reconcile_calls == expected

    # The final 256 s step lands at cumulative 510 s, crossing the 300 s
    # background tick: one burst send plus one tick send.
    await _advance(hass, freezer, RECONCILE_BURST_DELAYS_SECONDS[-1])
    expected += 2
    assert websocket.reconcile_calls == expected

    # Burst exhausted. The background tick that fired late (at 510 s)
    # rescheduled itself for 810 s, so advancing to 600 s sends nothing.
    await _advance(hass, freezer, 90)
    assert websocket.reconcile_calls == expected

    # Crossing 810 s: background tick only — no ninth burst step exists.
    await _advance(hass, freezer, 300)
    expected += 1
    assert websocket.reconcile_calls == expected


@pytest.mark.asyncio
async def test_new_write_restarts_burst(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Awaitable[ConfigEntry]],
) -> None:
    """Reset the burst schedule, cancelling pending steps, on a new write."""
    config_entry = await setup_integration()
    coordinator = config_entry.runtime_data
    websocket = carrier_api.api_websocket
    websocket.reconcile_calls = 0

    await coordinator.async_perform_api_call("first write", _noop_write)
    await _advance(hass, freezer, 2)
    await _advance(hass, freezer, 4)
    assert websocket.reconcile_calls == 2

    # Second write at t=6: the old chain's next step would fire at t=14.
    await coordinator.async_perform_api_call("second write", _noop_write)
    await _advance(hass, freezer, 2)
    assert websocket.reconcile_calls == 3
    await _advance(hass, freezer, 4)
    assert websocket.reconcile_calls == 4

    # t=14: the cancelled first chain must not fire an extra send.
    await _advance(hass, freezer, 2)
    assert websocket.reconcile_calls == 4

    # t=20: the restarted chain's 8 s step fires.
    await _advance(hass, freezer, 6)
    assert websocket.reconcile_calls == 5


@pytest.mark.asyncio
async def test_reconcile_skipped_without_websocket(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Awaitable[ConfigEntry]],
) -> None:
    """Skip sending quietly when no websocket client exists."""
    await setup_integration()
    websocket = carrier_api.api_websocket
    websocket.reconcile_calls = 0
    carrier_api.api_websocket = None

    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS)
    assert websocket.reconcile_calls == 0


@pytest.mark.asyncio
async def test_reconcile_send_errors_are_swallowed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Awaitable[ConfigEntry]],
) -> None:
    """Keep the schedule alive when a reconcile send fails in transport."""
    await setup_integration()
    websocket = carrier_api.api_websocket
    websocket.reconcile_calls = 0
    websocket.reconcile_errors.append(CarrierApiWebsocketError("send failed"))

    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS)
    assert websocket.reconcile_calls == 0

    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS)
    assert websocket.reconcile_calls == 1


@pytest.mark.asyncio
async def test_setup_failure_after_start_stops_reconcile_tick(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Leave no running tick behind when setup fails after the tick started."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        version=2,
    )
    config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform setup failed"),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    websocket = carrier_api.api_websocket
    websocket.reconcile_calls = 0
    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS)
    assert websocket.reconcile_calls == 0


@pytest.mark.asyncio
async def test_unload_cancels_reconcile_timers(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    carrier_api: FakeCarrierApiConnection,
    setup_integration: Callable[..., Awaitable[ConfigEntry]],
) -> None:
    """Stop both the background tick and any pending burst on unload."""
    config_entry = await setup_integration()
    coordinator = config_entry.runtime_data
    websocket = carrier_api.api_websocket

    await coordinator.async_perform_api_call("test write", _noop_write)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    websocket.reconcile_calls = 0
    await _advance(hass, freezer, RECONCILE_BACKGROUND_INTERVAL_SECONDS * 2)
    assert websocket.reconcile_calls == 0
