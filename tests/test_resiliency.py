"""Workflow tests for Carrier retry and resiliency helpers."""

from __future__ import annotations

import logging

from carrier_api import CarrierApiAuthError, CarrierApiConnectionError, CarrierApiGraphqlError
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
async def test_retry_helper_retries_graphql_failure_then_succeeds(
    retry_policy: RetryPolicy,
) -> None:
    """Retry a rejected GraphQL operation instead of failing the whole cycle."""
    state = ResiliencyState(unauthorized_threshold=3, transient_threshold=3)
    attempts = 0

    async def operation() -> str:
        """Fail once with a GraphQL rejection, then succeed."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise CarrierApiGraphqlError("Carrier GraphQL operation failed: getInfinitySystems")
        return "ok"

    result = await async_call_with_retry(
        operation,
        policy=retry_policy,
        state=state,
        operation_name="full data refresh",
        logger=logging.getLogger(__name__),
    )

    assert result == "ok"
    assert attempts == 2
    assert state.consecutive_transient == 0


@pytest.mark.asyncio
async def test_retry_helper_escalates_repeated_graphql_failures(
    retry_policy: RetryPolicy,
) -> None:
    """Surface a sustained GraphQL outage instead of retrying it forever."""
    state = ResiliencyState(unauthorized_threshold=3, transient_threshold=2)
    attempts = 0

    async def operation() -> str:
        """Always fail with a GraphQL rejection."""
        nonlocal attempts
        attempts += 1
        raise CarrierApiGraphqlError("Carrier GraphQL operation failed: getInfinitySystems")

    with pytest.raises(CarrierApiGraphqlError):
        await async_call_with_retry(
            operation,
            policy=retry_policy,
            state=state,
            operation_name="full data refresh",
            logger=logging.getLogger(__name__),
        )

    assert attempts == 2
    assert state.consecutive_transient == 2


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


@pytest.mark.asyncio
async def test_unmanaged_unauthorized_error_is_reraised_without_retry() -> None:
    """Re-raise a 401 untouched when the policy does not retry and state is unmanaged."""
    state = ResiliencyState(unauthorized_threshold=1, transient_threshold=3)
    policy = RetryPolicy(
        name="test",
        max_attempts=3,
        base_delay=0,
        max_delay=0,
        jitter_fraction=0,
        retry_on_unauthorized=False,
    )
    calls = 0

    async def operation() -> None:
        """Fail with unauthorized."""
        nonlocal calls
        calls += 1
        raise CarrierApiAuthError("unauthorized")

    with pytest.raises(CarrierApiAuthError):
        await async_call_with_retry(
            operation,
            policy=policy,
            state=state,
            operation_name="read",
            logger=logging.getLogger(__name__),
            manage_unauthorized_state=False,
        )

    assert calls == 1
    assert state.consecutive_unauthorized == 0


@pytest.mark.asyncio
async def test_retrying_unauthorized_policy_gives_up_after_max_attempts() -> None:
    """Raise CarrierUnauthorizedError once a write policy runs out of 401 retries."""
    state = ResiliencyState(unauthorized_threshold=10, transient_threshold=3)
    policy = RetryPolicy(
        name="test",
        max_attempts=2,
        base_delay=0,
        max_delay=0,
        jitter_fraction=0,
        retry_on_unauthorized=True,
    )
    calls = 0

    async def operation() -> None:
        """Fail with unauthorized."""
        nonlocal calls
        calls += 1
        raise CarrierApiAuthError("unauthorized")

    with pytest.raises(CarrierUnauthorizedError, match="after 2 retries"):
        await async_call_with_retry(
            operation,
            policy=policy,
            state=state,
            operation_name="write",
            logger=logging.getLogger(__name__),
        )

    assert calls == 2


@pytest.mark.asyncio
async def test_transient_escalation_and_non_retryable_errors_are_reraised() -> None:
    """Re-raise once transient failures escalate, and never retry other errors."""
    policy = RetryPolicy(
        name="test",
        max_attempts=5,
        base_delay=0,
        max_delay=0,
        jitter_fraction=0,
        retry_on_unauthorized=False,
    )

    async def offline() -> None:
        """Fail with a transient transport error."""
        raise CarrierApiConnectionError("offline")

    with pytest.raises(CarrierApiConnectionError):
        await async_call_with_retry(
            offline,
            policy=policy,
            state=ResiliencyState(unauthorized_threshold=3, transient_threshold=1),
            operation_name="read",
            logger=logging.getLogger(__name__),
        )

    async def broken() -> None:
        """Fail with a programming error."""
        raise ValueError("bad payload")

    with pytest.raises(ValueError, match="bad payload"):
        await async_call_with_retry(
            broken,
            policy=policy,
            state=ResiliencyState(unauthorized_threshold=3, transient_threshold=3),
            operation_name="read",
            logger=logging.getLogger(__name__),
        )
