"""Workflow tests for Carrier diagnostics output."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest

from custom_components.ha_carrier.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_redacts_config_entry_and_includes_device_entities(
    hass: HomeAssistant,
    setup_integration: Callable[..., Any],
) -> None:
    """Build diagnostics from a loaded entry with redacted sensitive data."""
    config_entry = await setup_integration()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["entry"]["data"][CONF_USERNAME] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["ABC123"]["mapped_data"]["serial"] == "**REDACTED**"
    assert diagnostics["ABC123"]["device"]["entities"]
