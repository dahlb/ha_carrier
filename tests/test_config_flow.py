"""Workflow tests for Carrier config and options flows."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

from aiohttp import ClientError
from carrier_api import AuthError, BaseError
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_carrier import config_flow
from custom_components.ha_carrier.const import (
    CONF_INFINITE_HOLDS,
    DOMAIN,
    ERROR_AUTH,
    ERROR_CANNOT_CONNECT,
    ERROR_UNKNOWN,
)

from .conftest import PASSWORD, USERNAME, FakeCarrierApiConnection


@pytest.mark.asyncio
async def test_user_flow_creates_entry_after_successful_validation(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Validate credentials and create a config entry from the user flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
async def test_user_flow_aborts_duplicate_account(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Abort a user flow when the Carrier username is already configured."""
    config_entry = MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={})
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (AuthError("unauthorized"), ERROR_AUTH),
        (ClientError("temporary"), ERROR_CANNOT_CONNECT),
        (BaseError("unexpected"), ERROR_UNKNOWN),
    ],
)
async def test_user_flow_maps_validation_errors(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    error: BaseException,
    expected_error: str,
) -> None:
    """Map Carrier credential validation failures to flow error keys."""
    patch_carrier_api.load_data_error = error

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


@pytest.mark.asyncio
async def test_reauth_flow_updates_password(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Validate a new password and update the existing config entry."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: "old"},
    )
    config_entry.add_to_hass(hass)

    form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": config_entry.entry_id},
        data=config_entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        user_input={CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == PASSWORD
    assert patch_carrier_api.password == PASSWORD


@pytest.mark.asyncio
async def test_options_flow_updates_infinite_hold_option(hass: HomeAssistant) -> None:
    """Create options data from the Carrier options flow."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options={CONF_INFINITE_HOLDS: True},
    )
    config_entry.add_to_hass(hass)

    form = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        user_input={CONF_INFINITE_HOLDS: False},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_INFINITE_HOLDS: False}


def test_reauth_rejects_credentials_for_different_unconfigured_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test reauth rejects credentials for a different unconfigured Carrier account."""

    async def async_validate_credentials(
        username: str,
        password: str,
    ) -> tuple[dict[str, str], str | None]:
        """Return a validated identity that does not belong to the reauth entry.

        Args:
            username: Submitted Carrier account username.
            password: Submitted Carrier account password.

        Returns:
            tuple[dict[str, str], str | None]: No errors and a mismatched identity ID.
        """
        return {}, "new-identity"

    async def async_set_unique_id(unique_id: str) -> None:
        """Set the flow unique ID without requiring a Home Assistant instance.

        Args:
            unique_id: Validated Carrier identity ID.
        """
        flow.context["unique_id"] = unique_id

    def async_update_reload_and_abort(*args: Any, **kwargs: Any) -> None:
        """Fail if reauth tries to retarget the entry to a new identity.

        Args:
            *args: Positional Home Assistant flow arguments.
            **kwargs: Keyword Home Assistant flow arguments.
        """
        pytest.fail("reauth retargeted the existing config entry")

    flow = config_flow.CarrierConfigFlow()
    flow.context = {"source": "reauth", "entry_id": "entry-1"}
    flow._reauth_entry_data = {
        CONF_USERNAME: "old@example.com",
        CONF_PASSWORD: "old-password",
    }
    reauth_entry = SimpleNamespace(
        entry_id="entry-1",
        unique_id="saved-identity",
        data=flow._reauth_entry_data,
        title="old@example.com",
    )

    monkeypatch.setattr(config_flow, "_async_validate_credentials", async_validate_credentials)
    monkeypatch.setattr(flow, "_get_reauth_entry", lambda: reauth_entry)
    monkeypatch.setattr(flow, "async_set_unique_id", async_set_unique_id)
    monkeypatch.setattr(flow, "async_update_reload_and_abort", async_update_reload_and_abort)

    with pytest.raises(data_entry_flow.AbortFlow) as abort_flow:
        _run_async(
            flow.async_step_reauth_confirm(
                {
                    CONF_USERNAME: "new@example.com",
                    CONF_PASSWORD: "new-password",
                }
            )
        )

    assert abort_flow.value.reason == "unique_id_mismatch"


def _run_async[T](awaitable: Coroutine[Any, Any, T]) -> T:
    """Run an awaitable in a fresh event loop.

    Args:
        awaitable: Awaitable test target.

    Returns:
        T: Awaitable result.
    """
    return asyncio.run(awaitable)
