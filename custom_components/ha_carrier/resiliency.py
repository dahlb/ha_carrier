"""Centralized retry policies, escalation state, and retry helper.

A single coordinator-owned `ResiliencyState` instance is shared across reads,
writes, and the websocket loop so a 401 seen on any surface increments the same
counter the others check. `async_call_with_retry` is the one funnel for bounded,
classified retries; the websocket loop reuses `RetryPolicy` and
`compute_backoff_delay` directly because it manages its own long-running state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
from math import ceil, log2
import random

from .exceptions import CarrierUnauthorizedError
from .util import is_transient_transport_error, is_unauthorized_error

UNAUTHORIZED_STATE_ATTRIBUTES: tuple[str, ...] = (
    "unauthorized_threshold",
    "consecutive_unauthorized",
    "unauthorized_outage_logged",
    "unauthorized_escalated_logged",
)


@dataclass(frozen=True)
class RetryPolicy:
    """Declarative retry behavior for one logical operation type.

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

    Attributes:
        unauthorized_threshold: Consecutive 401 count that triggers escalation.
        transient_threshold: Consecutive transient failure count that triggers
            escalation.
    """

    unauthorized_threshold: int
    transient_threshold: int
    consecutive_unauthorized: int = 0
    consecutive_transient: int = 0
    unauthorized_outage_logged: bool = False
    unauthorized_escalated_logged: bool = False
    transient_outage_logged: bool = False
    transient_escalated_logged: bool = False
    suppress_recording: bool = field(default=False, repr=False)

    def reset(self) -> None:
        """Zero counters and log flags after any successful operation."""
        self.reset_unauthorized()
        self.reset_transient()
        # suppress_recording is a stable config flag, not a counter — intentionally not reset here

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

    @contextmanager
    def preserve_unauthorized(self) -> Iterator[None]:
        """Restore unauthorized counters and log flags after a guarded block.

        Yields:
            None: Control to the guarded block.
        """
        unauthorized_state = {
            attribute_name: getattr(self, attribute_name)
            for attribute_name in UNAUTHORIZED_STATE_ATTRIBUTES
        }
        try:
            yield
        finally:
            for name, value in unauthorized_state.items():
                setattr(self, name, value)

    def record_unauthorized(self, logger: logging.Logger, operation_name: str) -> bool:
        """Record a 401 response and decide whether escalation has occurred.

        Args:
            logger: Logger used for outage / escalation messages.
            operation_name: Short description of the request path that failed.

        Returns:
            bool: True when the count has reached or exceeded the threshold.
        """
        if self.suppress_recording:
            return self.consecutive_unauthorized >= self.unauthorized_threshold
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
        """Record a transient transport failure and decide whether escalation has occurred.

        Args:
            logger: Logger used for outage / escalation messages.
            operation_name: Short description of the request path that failed.
            error: The transient exception that was caught.

        Returns:
            bool: True when the count has reached or exceeded the threshold.
        """
        if self.suppress_recording:
            return self.consecutive_transient >= self.transient_threshold
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
    reset_unauthorized_on_success: bool = True,
    reset_transient_on_success: bool = True,
) -> T:
    """Run `operation` with classification-driven retry and shared escalation state.

    On success: the selected portions of `state` are reset and the result is returned.
    On unauthorized: state.record_unauthorized() is called when enabled; if the
    unauthorized count escalates, raises CarrierUnauthorizedError. Otherwise,
    unauthorized retry follows the policy and a non-escalated 401 re-raises the
    original exception.
    On transient transport error: state.record_transient() is called; on
    escalation the original error is raised; otherwise the helper sleeps a
    computed backoff and retries until attempts are exhausted.
    Other exceptions propagate immediately. asyncio.CancelledError,
    KeyboardInterrupt, SystemExit are always re-raised.

    Args:
        operation: Awaitable callable performing the underlying API call.
        policy: Retry policy controlling attempts, backoff, and 401 handling.
        state: Coordinator-shared escalation counters.
        operation_name: Friendly name used in logs and escalation messages.
        logger: Logger used for outage / escalation messages.
        manage_unauthorized_state: Whether this call should increment / inspect
            shared unauthorized escalation state.
        reset_unauthorized_on_success: Whether a successful call should clear
            shared unauthorized tracking.
        reset_transient_on_success: Whether a successful call should clear
            shared transient tracking.

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
            if reset_unauthorized_on_success and reset_transient_on_success:
                state.reset()
            else:
                if reset_unauthorized_on_success:
                    state.reset_unauthorized()
                if reset_transient_on_success:
                    state.reset_transient()
            return result
