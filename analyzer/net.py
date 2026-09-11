from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SEC = 1.0
DEFAULT_MAX_BACKOFF_SEC = 30.0
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """Retry settings for one outbound call site."""

    attempts: int = DEFAULT_ATTEMPTS
    backoff_sec: float = DEFAULT_BACKOFF_SEC
    max_backoff_sec: float = DEFAULT_MAX_BACKOFF_SEC

    @classmethod
    def from_config(cls, cfg: dict | None, **overrides: Any) -> "RetryPolicy":
        cfg = cfg or {}
        values = {
            "attempts": int(cfg.get("attempts", DEFAULT_ATTEMPTS)),
            "backoff_sec": float(cfg.get("backoff_sec", DEFAULT_BACKOFF_SEC)),
            "max_backoff_sec": float(cfg.get("max_backoff_sec", DEFAULT_MAX_BACKOFF_SEC)),
        }
        values.update(overrides)
        return cls(**values)


class RetryExhaustedError(RuntimeError):
    """All attempts failed; carries the last underlying error for diagnostics."""


def backoff_delay(
    attempt: int,
    backoff_sec: float = DEFAULT_BACKOFF_SEC,
    max_backoff_sec: float = DEFAULT_MAX_BACKOFF_SEC,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter, capped.

    ``attempt`` is 1-based. Jitter spreads retries so that a shared upstream
    outage does not turn into synchronised retry bursts. ``rng`` is injectable so
    tests can assert the bounds instead of relying on probability.
    """
    base = min(backoff_sec * (2 ** (attempt - 1)), max_backoff_sec)
    generator = rng or random
    return generator.uniform(0.0, base)


def request_with_retry(
    request: Callable[[], requests.Response],
    *,
    method: str,
    policy: RetryPolicy | None = None,
    rng: random.Random | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """Run an HTTP request with bounded retries and jittered backoff.

    Retries transport errors and retryable status codes. A 4xx that is not
    retryable is returned as-is so the caller's own error handling decides;
    retrying it would only waste the budget.
    """
    policy = policy or RetryPolicy()
    attempts = max(1, int(policy.attempts))
    backoff_sec = policy.backoff_sec
    max_backoff_sec = policy.max_backoff_sec
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = request()
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = backoff_delay(attempt, backoff_sec, max_backoff_sec, rng)
            logger.warning(
                "%s request failed (attempt %d/%d), retrying in %.2fs: %s",
                method,
                attempt,
                attempts,
                delay,
                type(exc).__name__,
            )
            sleep(delay)
            continue

        if response.status_code in RETRYABLE_STATUS and attempt < attempts:
            delay = backoff_delay(attempt, backoff_sec, max_backoff_sec, rng)
            logger.warning(
                "%s returned HTTP %d (attempt %d/%d), retrying in %.2fs",
                method,
                response.status_code,
                attempt,
                attempts,
                delay,
            )
            sleep(delay)
            continue
        return response

    raise RetryExhaustedError(
        f"{method} failed after {attempts} attempt(s): {type(last_error).__name__ if last_error else 'unknown'}"
    ) from last_error
