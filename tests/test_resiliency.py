"""Workflow tests for Carrier retry and resiliency helpers."""

from __future__ import annotations

import logging

from carrier_api import CarrierApiAuthError, CarrierApiConnectionError
import pytest

from custom_components.ha_carrier.exceptions import CarrierUnauthorizedError
from custom_components.ha_carrier.resiliency import (
    ResiliencyState,
    RetryPolicy,
    async_call_with_retry,
    compute_backoff_delay,
)


@pytest.fixture
def retry_policy() -> RetryPolicy:
    """Return a deterministic retry policy for tests."""
    return RetryPolicy(
        name="test",
        max_attempts=3,
        base_delay=0,
        max_delay=0,
        jitter_fraction=0,
        retry_on_unauthorized=True,
    )


@pytest.mark.asyncio
async def test_retry_helper_retries_transient_failure_then_resets_state(
    retry_policy: RetryPolicy,
) -> None:
    """Retry transient failures and clear counters after a later success."""
    state = ResiliencyState(unauthorized_threshold=3, transient_threshold=3)
    attempts = 0

    async def operation() -> str:
        """Fail once with a transient error, then succeed."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise CarrierApiConnectionError("temporary")
        return "ok"

    result = await async_call_with_retry(
        operation,
        policy=retry_policy,
        state=state,
        operation_name="test operation",
        logger=logging.getLogger(__name__),
    )

    assert result == "ok"
    assert attempts == 2
    assert state.consecutive_transient == 0


@pytest.mark.asyncio
async def test_retry_helper_escalates_repeated_unauthorized_failures(
    retry_policy: RetryPolicy,
) -> None:
    """Raise CarrierUnauthorizedError after unauthorized failures cross threshold."""
    state = ResiliencyState(unauthorized_threshold=2, transient_threshold=3)

    async def operation() -> None:
        """Always raise an unauthorized Carrier transport error."""
        raise CarrierApiAuthError("unauthorized")

    with pytest.raises(CarrierUnauthorizedError):
        await async_call_with_retry(
            operation,
            policy=retry_policy,
            state=state,
            operation_name="test operation",
            logger=logging.getLogger(__name__),
        )

    assert state.consecutive_unauthorized == 2


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(0, 1.0), (1, 2.0), (2, 4.0), (5, 4.0)],
)
def test_compute_backoff_delay_caps_exponential_delay(attempt: int, expected: float) -> None:
    """Cap exponential backoff at the policy maximum."""
    policy = RetryPolicy(
        name="test",
        max_attempts=None,
        base_delay=1.0,
        max_delay=4.0,
        jitter_fraction=0,
        retry_on_unauthorized=False,
    )

    assert compute_backoff_delay(policy, attempt) == expected
