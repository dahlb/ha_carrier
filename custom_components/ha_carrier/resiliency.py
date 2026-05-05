"""Retry policies and shared escalation state for Carrier API operations.

Carrier can return short-lived 401s and transient transport failures during
service outages, so the integration does not treat the first failure as a hard
reauth or permanent outage. Each coordinator owns one `ResiliencyState` shared
by refresh and write API calls. Websocket failures request a refresh instead of
changing counters directly, so the refresh path remains the owner of outage
accounting. A later successful API operation clears stale counters unless the
caller is intentionally doing cycle-level accounting.

`async_call_with_retry` is the shared helper for bounded API calls. The websocket
loop reuses `RetryPolicy` and `compute_backoff_delay` directly because it manages
its own long-running listener and reconnect cycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from math import ceil, log2
import random

from .exceptions import CarrierUnauthorizedError
from .util import is_transient_transport_error, is_unauthorized_error


@dataclass(frozen=True)
class RetryPolicy:
    """Declarative retry behavior for one logical API operation type.

    The policy describes how one call retries after a classified failure. It
    does not own escalation counters; those live in `ResiliencyState` so
    refreshes and writes contribute to the same outage window.

    Attributes:
        name: Short identifier used in log messages.
        max_attempts: Total attempts including the first call. None = unbounded.
        base_delay: Initial backoff delay in seconds (before jitter).
        max_delay: Maximum backoff delay in seconds (after exponentiation).
        jitter_fraction: Symmetric jitter as a fraction of the computed delay.
        retry_on_unauthorized: When True, a 401 inside the threshold is retried
            in-place with backoff; when False, non-escalated unauthorized
            errors are re-raised to the caller and only threshold escalation
            raises CarrierUnauthorizedError.
        retry_on_transient: When True, transient transport errors are retried.
    """

    name: str
    max_attempts: int | None
    base_delay: float
    max_delay: float
    jitter_fraction: float
    retry_on_unauthorized: bool
    retry_on_transient: bool = True


@dataclass
class ResiliencyState:
    """Cross-call escalation counters shared across coordinator surfaces.

    Unauthorized and transient failures are tracked separately because they
    escalate to different Home Assistant behavior: persistent 401s trigger
    reauthentication, while persistent transport failures surface as retryable
    update/write failures. A successful normal operation resets both counters.
    Callers that need cycle-level accounting, such as per-system energy refresh,
    can defer success resets and record the cycle result explicitly.

    Attributes:
        unauthorized_threshold: Consecutive 401 count that triggers escalation.
        transient_threshold: Consecutive transient failure count that triggers
            escalation.
        consecutive_unauthorized: Current count of consecutive 401-style
            failures in the shared outage window.
        consecutive_transient: Current count of consecutive transient transport
            failures in the shared outage window.
        unauthorized_outage_logged: Whether the current unauthorized outage has
            already emitted its first log message.
        unauthorized_escalated_logged: Whether the current unauthorized outage
            has already emitted its escalation log message.
        transient_outage_logged: Whether the current transient outage has
            already emitted its first log message.
        transient_escalated_logged: Whether the current transient outage has
            already emitted its escalation log message.
    """

    unauthorized_threshold: int
    transient_threshold: int
    consecutive_unauthorized: int = 0
    consecutive_transient: int = 0
    unauthorized_outage_logged: bool = False
    unauthorized_escalated_logged: bool = False
    transient_outage_logged: bool = False
    transient_escalated_logged: bool = False

    def reset(self) -> None:
        """Clear all retry counters and log flags after a normal success."""
        self.reset_unauthorized()
        self.reset_transient()

    def reset_unauthorized(self) -> None:
        """Clear only unauthorized counters and related log flags."""
        self.consecutive_unauthorized = 0
        self.unauthorized_outage_logged = False
        self.unauthorized_escalated_logged = False

    def reset_transient(self) -> None:
        """Clear only transient counters and related log flags."""
        self.consecutive_transient = 0
        self.transient_outage_logged = False
        self.transient_escalated_logged = False

    def record_unauthorized(self, logger: logging.Logger, operation_name: str) -> bool:
        """Record a 401 response and decide whether escalation has occurred.

        Args:
            logger: Logger used for outage / escalation messages.
            operation_name: Short description of the request path that failed.

        Returns:
            bool: True when the count has reached or exceeded the threshold.
        """
        self.consecutive_unauthorized += 1
        if not self.unauthorized_outage_logged:
            logger.info(
                "Carrier API returned unauthorized during %s; treating it as a transient blip.",
                operation_name,
            )
            self.unauthorized_outage_logged = True
        escalated = self.consecutive_unauthorized >= self.unauthorized_threshold
        if escalated and not self.unauthorized_escalated_logged:
            logger.error(
                "Carrier API returned unauthorized %s consecutive times during %s; "
                "this no longer looks transient.",
                self.consecutive_unauthorized,
                operation_name,
            )
            self.unauthorized_escalated_logged = True
        return escalated

    def record_transient(
        self,
        logger: logging.Logger,
        operation_name: str,
        error: BaseException,
    ) -> bool:
        """Record a transient transport failure and decide whether it escalated.

        Args:
            logger: Logger used for outage / escalation messages.
            operation_name: Short description of the request path that failed.
            error: The transient exception that was caught.

        Returns:
            bool: True when the count has reached or exceeded the threshold.
        """
        self.consecutive_transient += 1
        if not self.transient_outage_logged:
            logger.info(
                "Carrier API transient failure during %s (%s: %s); will retry with backoff.",
                operation_name,
                type(error).__name__,
                error,
            )
            self.transient_outage_logged = True
        escalated = self.consecutive_transient >= self.transient_threshold
        if escalated and not self.transient_escalated_logged:
            logger.error(
                "Carrier API has now had %s consecutive transient failures during %s; "
                "surfacing as an outage.",
                self.consecutive_transient,
                operation_name,
            )
            self.transient_escalated_logged = True
        return escalated


def compute_backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """Return the next backoff delay for a given policy and 0-indexed attempt.

    Computes `base_delay * 2 ** attempt`, clamps to `max_delay`, and applies
    symmetric jitter of `±jitter_fraction * delay`.

    Args:
        policy: Retry policy whose timing parameters drive the calculation.
        attempt: Zero-based attempt number (0 = first retry after first failure).

    Returns:
        float: Non-negative delay in seconds.
    """
    if policy.base_delay > 0 and policy.max_delay > policy.base_delay:
        max_exponent = ceil(log2(policy.max_delay / policy.base_delay))
    else:
        max_exponent = 0
    exponent = min(max(attempt, 0), max_exponent)
    raw = policy.base_delay * (2**exponent)
    capped = min(raw, policy.max_delay)
    if policy.jitter_fraction <= 0:
        return capped
    jitter = capped * policy.jitter_fraction
    return max(0.0, capped + random.uniform(-jitter, jitter))  # noqa: S311


async def async_call_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    state: ResiliencyState,
    operation_name: str,
    logger: logging.Logger,
    manage_unauthorized_state: bool = True,
    reset_state_on_success: bool = True,
) -> T:
    """Run `operation` with classification-driven retry and shared escalation state.

    The helper classifies failures before deciding whether to retry, escalate,
    or re-raise. Unauthorized failures optionally update the shared auth counter;
    once the threshold is crossed, `CarrierUnauthorizedError` is raised so the
    coordinator can trigger Home Assistant reauth. Non-escalated 401s either
    retry in-place when the policy allows it or propagate to the caller for
    cycle-level handling.

    Transient transport failures update the shared transient counter. They retry
    with exponential backoff until the policy's attempt limit is reached or the
    shared transient threshold escalates, in which case the original error is
    raised.

    A successful call normally resets all shared resiliency state. Callers set
    `reset_state_on_success=False` only when one logical operation is made from
    multiple helper calls and will record/reset state after the full cycle. Other
    exceptions propagate immediately. `asyncio.CancelledError`, `KeyboardInterrupt`,
    and `SystemExit` are always re-raised.

    Args:
        operation: Awaitable callable performing the underlying API call.
        policy: Retry policy controlling attempts, backoff, and 401 handling.
        state: Coordinator-shared escalation counters.
        operation_name: Friendly name used in logs and escalation messages.
        logger: Logger used for outage / escalation messages.
        manage_unauthorized_state: Whether this call should increment / inspect
            shared unauthorized escalation state.
        reset_state_on_success: Whether a successful call should clear shared
            resiliency tracking. Set False only for cycle-scoped callers that
            finalize shared state outside the helper.

    Returns:
        T: The result returned by `operation` on success.

    Raises:
        CarrierUnauthorizedError: When 401s escalate beyond the shared threshold.
        BaseException: Any non-retryable error from `operation`, or the last
            transient error after attempts are exhausted or escalated.
    """
    attempt = 0
    while True:
        try:
            result = await operation()
        except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
            raise
        except Exception as error:
            if is_unauthorized_error(error):
                if manage_unauthorized_state:
                    escalated = state.record_unauthorized(logger, operation_name)
                else:
                    escalated = False
                if escalated:
                    raise CarrierUnauthorizedError(
                        f"Carrier API rejected {operation_name} as unauthorized."
                    ) from error
                if policy.retry_on_unauthorized:
                    if policy.max_attempts is not None and attempt + 1 >= policy.max_attempts:
                        raise
                    await asyncio.sleep(compute_backoff_delay(policy, attempt))
                    attempt += 1
                    continue
                raise
            if policy.retry_on_transient and is_transient_transport_error(error):
                escalated = state.record_transient(logger, operation_name, error)
                if escalated:
                    raise
                if policy.max_attempts is not None and attempt + 1 >= policy.max_attempts:
                    raise
                await asyncio.sleep(compute_backoff_delay(policy, attempt))
                attempt += 1
                continue
            raise
        else:
            if reset_state_on_success:
                state.reset()
            return result
