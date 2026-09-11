from __future__ import annotations

import random
import unittest
from unittest.mock import Mock

import requests

from analyzer.net import (
    RetryPolicy,
    RetryExhaustedError,
    backoff_delay,
    request_with_retry,
)


def _response(status: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    return response


class BackoffTests(unittest.TestCase):
    def test_delay_stays_within_the_exponential_bound(self) -> None:
        rng = random.Random(1234)
        for attempt in (1, 2, 3, 4):
            cap = min(1.0 * (2 ** (attempt - 1)), 30.0)
            for _ in range(50):
                delay = backoff_delay(attempt, 1.0, 30.0, rng)
                self.assertGreaterEqual(delay, 0.0)
                self.assertLessEqual(delay, cap)

    def test_delay_is_capped(self) -> None:
        rng = random.Random(7)
        for _ in range(50):
            self.assertLessEqual(backoff_delay(20, 1.0, 5.0, rng), 5.0)

    def test_jitter_actually_varies(self) -> None:
        rng = random.Random(99)
        samples = {round(backoff_delay(3, 1.0, 30.0, rng), 6) for _ in range(50)}
        self.assertGreater(len(samples), 1, "backoff must jitter, not be a fixed ramp")


class RequestWithRetryTests(unittest.TestCase):
    def test_returns_on_first_success(self) -> None:
        sleeps: list[float] = []
        result = request_with_retry(
            lambda: _response(200), method="t", policy=RetryPolicy(attempts=3), sleep=sleeps.append
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(sleeps, [])

    def test_retries_transport_errors_then_succeeds(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []

        def flaky() -> requests.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("boom")
            return _response(200)

        result = request_with_retry(
            flaky, method="t", policy=RetryPolicy(attempts=4, backoff_sec=0.01), sleep=sleeps.append
        )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(sleeps), 2, "one backoff between each retry")

    def test_retries_retryable_status_codes(self) -> None:
        statuses = [503, 429, 200]
        sleeps: list[float] = []
        result = request_with_retry(
            lambda: _response(statuses.pop(0)),
            method="t",
            policy=RetryPolicy(attempts=4, backoff_sec=0.01),
            sleep=sleeps.append,
        )
        self.assertEqual(result.status_code, 200)

    def test_does_not_retry_a_plain_4xx(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []

        def not_found() -> requests.Response:
            calls["n"] += 1
            return _response(404)

        result = request_with_retry(
            not_found, method="t", policy=RetryPolicy(attempts=5), sleep=sleeps.append
        )

        self.assertEqual(result.status_code, 404)
        self.assertEqual(calls["n"], 1, "a 404 must be returned to the caller, not retried")
        self.assertEqual(sleeps, [])

    def test_exhausts_and_raises_with_the_attempt_count(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []

        def always_down() -> requests.Response:
            calls["n"] += 1
            raise requests.Timeout("down")

        with self.assertRaises(RetryExhaustedError) as ctx:
            request_with_retry(
                always_down,
                method="article",
                policy=RetryPolicy(attempts=3, backoff_sec=0.01),
                sleep=sleeps.append,
            )

        self.assertEqual(calls["n"], 3, "attempts must be bounded")
        self.assertEqual(len(sleeps), 2, "no sleep after the final attempt")
        self.assertIn("3 attempt", str(ctx.exception))

    def test_policy_from_config_reads_overrides(self) -> None:
        policy = RetryPolicy.from_config({"attempts": 5, "backoff_sec": 0.25, "max_backoff_sec": 9})
        self.assertEqual((policy.attempts, policy.backoff_sec, policy.max_backoff_sec), (5, 0.25, 9.0))

    def test_default_policy_applies_when_config_is_absent(self) -> None:
        self.assertEqual(RetryPolicy.from_config(None), RetryPolicy())


if __name__ == "__main__":
    unittest.main()
