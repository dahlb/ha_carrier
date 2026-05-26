"""Workflow tests for Carrier config and options flows."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

from carrier_api import CarrierApiAuthError, CarrierApiConnectionError, CarrierApiGraphqlError
from homeassistant import config_entries, data_entry_flow
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
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

from .conftest import IDENTITY_ID, PASSWORD, USERNAME, FakeCarrierApiConnection


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
    assert result["result"].unique_id == IDENTITY_ID
    assert patch_carrier_api.cleanup_calls == 1


@pytest.mark.asyncio
async def test_user_flow_aborts_duplicate_account(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Abort a user flow when the Carrier username is already configured."""
    config_entry = MockConfigEntry(domain=DOMAIN, unique_id=IDENTITY_ID, data={})
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
        (CarrierApiAuthError("unauthorized"), ERROR_AUTH),
        (CarrierApiConnectionError("temporary"), ERROR_CANNOT_CONNECT),
        (TimeoutError("temporary"), ERROR_CANNOT_CONNECT),
        (OSError("temporary"), ERROR_CANNOT_CONNECT),
        (CarrierApiGraphqlError("unexpected"), ERROR_UNKNOWN),
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
async def test_user_flow_still_creates_entry_when_cleanup_reports_carrier_error(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not fail a validated user flow only because API cleanup failed."""

    async def cleanup() -> None:
        """Raise the Carrier API cleanup failure."""
        raise CarrierApiConnectionError("cleanup failed")

    monkeypatch.setattr(patch_carrier_api, "cleanup", cleanup)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == IDENTITY_ID


@pytest.mark.asyncio
async def test_reauth_flow_updates_password(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Validate a new password and update the existing config entry."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=IDENTITY_ID,
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
    await _async_unload_loaded_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_reauth_flow_updates_username_and_reuses_blank_password(
    hass: HomeAssistant,
    patch_carrier_api: FakeCarrierApiConnection,
) -> None:
    """Validate a new username and keep the existing password when blank."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=IDENTITY_ID,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    config_entry.add_to_hass(hass)
    new_username = "new@example.com"

    form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": config_entry.entry_id},
        data=config_entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        user_input={CONF_USERNAME: new_username},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.title == new_username
    assert config_entry.data == {CONF_USERNAME: new_username, CONF_PASSWORD: PASSWORD}
    assert patch_carrier_api.username == new_username
    assert patch_carrier_api.password == PASSWORD
    await _async_unload_loaded_entry(hass, config_entry)


def test_user_flow_returns_unknown_when_validated_identity_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the user flow on the form when validation returns no identity."""

    async def async_validate_credentials(
        username: str,
        password: str,
    ) -> tuple[dict[str, str], str | None]:
        """Return a validation response without a Carrier identity."""
        return {}, None

    flow = config_flow.CarrierConfigFlow()
    flow.context = {"source": config_entries.SOURCE_USER}
    monkeypatch.setattr(config_flow, "_async_validate_credentials", async_validate_credentials)

    result = _run_async(flow.async_step_user({CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_UNKNOWN}


def test_reconfigure_updates_username_and_reuses_blank_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconfigure can update username while keeping the saved password."""
    reconfigure_entry = SimpleNamespace(
        entry_id="entry-1",
        unique_id=IDENTITY_ID,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        title=USERNAME,
    )
    updates: list[dict[str, Any]] = []

    async def async_validate_credentials(
        username: str,
        password: str,
    ) -> tuple[dict[str, str], str | None]:
        """Return a validated identity for the submitted credentials.

        Args:
            username: Submitted Carrier account username.
            password: Submitted Carrier account password.

        Returns:
            tuple[dict[str, str], str | None]: No errors and the existing identity ID.
        """
        assert username == "new@example.com"
        assert password == PASSWORD
        return {}, IDENTITY_ID

    async def async_set_unique_id(unique_id: str) -> None:
        """Set the flow unique ID without requiring a Home Assistant instance.

        Args:
            unique_id: Validated Carrier identity ID.
        """
        flow.context["unique_id"] = unique_id

    def async_update_reload_and_abort(*args: Any, **kwargs: Any) -> dict[str, str]:
        """Capture the requested reconfigure updates.

        Args:
            *args: Positional Home Assistant flow arguments.
            **kwargs: Keyword Home Assistant flow arguments.

        Returns:
            dict[str, str]: Minimal flow result.
        """
        updates.append({"args": args, **kwargs})
        return {"type": "abort", "reason": "reconfigure_successful"}

    flow = config_flow.CarrierConfigFlow()
    flow.context = {"source": "reconfigure", "entry_id": "entry-1"}

    monkeypatch.setattr(config_flow, "_async_validate_credentials", async_validate_credentials)
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: reconfigure_entry)
    monkeypatch.setattr(flow, "async_set_unique_id", async_set_unique_id)
    monkeypatch.setattr(flow, "async_update_reload_and_abort", async_update_reload_and_abort)

    result = _run_async(flow.async_step_reconfigure({CONF_USERNAME: "new@example.com"}))

    assert result["type"] == "abort"
    assert updates == [
        {
            "args": (reconfigure_entry,),
            "unique_id": IDENTITY_ID,
            "title": "new@example.com",
            "data_updates": {
                CONF_USERNAME: "new@example.com",
                CONF_PASSWORD: PASSWORD,
            },
        }
    ]


def test_reconfigure_returns_unknown_when_validated_identity_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep reconfigure on the form when validation returns no identity."""
    reconfigure_entry = SimpleNamespace(
        entry_id="entry-1",
        unique_id=IDENTITY_ID,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        title=USERNAME,
    )

    async def async_validate_credentials(
        username: str,
        password: str,
    ) -> tuple[dict[str, str], str | None]:
        """Return a validation response without a Carrier identity."""
        return {}, None

    flow = config_flow.CarrierConfigFlow()
    flow.context = {"source": "reconfigure", "entry_id": "entry-1"}
    monkeypatch.setattr(config_flow, "_async_validate_credentials", async_validate_credentials)
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: reconfigure_entry)

    result = _run_async(flow.async_step_reconfigure({CONF_USERNAME: USERNAME}))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_UNKNOWN}


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


def test_reauth_confirm_returns_unknown_when_validated_identity_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep reauth on the form when validation returns no identity."""
    reauth_entry = SimpleNamespace(
        entry_id="entry-1",
        unique_id=IDENTITY_ID,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        title=USERNAME,
    )

    async def async_validate_credentials(
        username: str,
        password: str,
    ) -> tuple[dict[str, str], str | None]:
        """Return a validation response without a Carrier identity."""
        return {}, None

    flow = config_flow.CarrierConfigFlow()
    flow.context = {"source": "reauth", "entry_id": "entry-1"}
    flow._reauth_entry_data = dict(reauth_entry.data)
    monkeypatch.setattr(config_flow, "_async_validate_credentials", async_validate_credentials)
    monkeypatch.setattr(flow, "_get_reauth_entry", lambda: reauth_entry)

    result = _run_async(flow.async_step_reauth_confirm({CONF_USERNAME: USERNAME}))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_UNKNOWN}


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


async def _async_unload_loaded_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Unload a reloaded config entry before test harness teardown.

    Args:
        hass: Home Assistant test instance.
        config_entry: Config entry that may have been reloaded by the flow.
    """
    await hass.async_block_till_done()
    if config_entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
