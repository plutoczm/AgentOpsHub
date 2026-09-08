"""Bounded retry delays with an injectable random draw and no internal sleeping."""

import math

from app.llm.config import RetryPolicy


def retry_delay(
    policy: RetryPolicy, failed_attempt: int, random_value: float, retry_after: float | None = None
) -> float:
    """Compute capped exponential/full-jitter delay; cap numeric Retry-After as well."""
    cap = min(policy.max_delay, policy.base_delay * (2 ** (failed_attempt - 1)))
    delay = cap * min(1.0, max(0.0, random_value)) if policy.jitter else cap
    if retry_after is not None and math.isfinite(retry_after) and retry_after >= 0:
        delay = min(policy.max_delay, max(delay, retry_after))
    return float(delay)
