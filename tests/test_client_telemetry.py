from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx

from generator.deepseek_client import DeepSeekClient, TokenUsage, _is_retryable


def _client(max_retries: int = 1) -> DeepSeekClient:
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
        return DeepSeekClient({"deepseek": {"max_retries": max_retries, "force_json_object": False}})


def _ok(payload: str = '{"ok": true}', prompt: int = 100, completion: int = 20) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


class SdkRetryLayeringTests(unittest.TestCase):
    def test_sdk_level_retries_are_disabled(self) -> None:
        """Otherwise the SDK retries under our loop and the attempt count lies."""
        client = _client()
        self.assertEqual(client.client.max_retries, 0)

    def test_request_timeout_is_bounded(self) -> None:
        """The SDK default read timeout is 600s, long enough to stall a run."""
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            client = DeepSeekClient(
                {"deepseek": {"max_retries": 1, "force_json_object": False, "timeout_sec": 42}}
            )
        self.assertEqual(float(client.client.timeout), 42.0)

    def test_attempt_count_is_exactly_the_outer_budget(self) -> None:
        client = _client(max_retries=2)
        calls = {"n": 0}

        def boom(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            raise httpx.ConnectError("down")

        with patch.object(client.client.chat.completions, "create", side_effect=boom), \
             patch("generator.deepseek_client.time.sleep"):
            with self.assertRaises(RuntimeError):
                client.chat_json("m", "s", "p")

        self.assertEqual(calls["n"], 3, "1 initial + 2 retries, with no SDK layer beneath")


class RetryClassificationTests(unittest.TestCase):
    def test_transport_errors_are_retryable(self) -> None:
        self.assertTrue(_is_retryable(httpx.ConnectError("x")))
        self.assertTrue(_is_retryable(httpx.ReadTimeout("x")))
        self.assertTrue(_is_retryable(httpx.RemoteProtocolError("x")))

    def test_rate_limit_is_retryable(self) -> None:
        class RateLimitError(Exception):
            pass

        self.assertTrue(_is_retryable(RateLimitError("slow down")))

    def test_openai_sdk_error_classes_are_retryable(self) -> None:
        import openai

        self.assertTrue(_is_retryable(openai.APIConnectionError(request=None)))  # type: ignore[arg-type]
        self.assertTrue(_is_retryable(openai.APITimeoutError(request=None)))  # type: ignore[arg-type]

    def test_caller_errors_are_not_retryable(self) -> None:
        self.assertFalse(_is_retryable(ValueError("invalid request: unknown parameter")))
        self.assertFalse(_is_retryable(KeyError("missing")))


class TelemetryTests(unittest.TestCase):
    def test_success_is_accumulated_with_stage_breakdown(self) -> None:
        client = _client()
        with patch.object(client.client.chat.completions, "create", return_value=_ok(prompt=1200, completion=300)):
            client.chat_json("m", "s", "p", stage="digest")

        usage = client.usage.to_dict()

        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["prompt_tokens"], 1200)
        self.assertEqual(usage["completion_tokens"], 300)
        self.assertEqual(usage["total_tokens"], 1500)
        self.assertIn("digest", usage["by_stage"])

    def test_failures_and_retries_are_counted(self) -> None:
        client = _client(max_retries=1)
        calls = {"n": 0}

        def boom(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            raise httpx.ConnectError("down")

        with patch.object(client.client.chat.completions, "create", side_effect=boom), \
             patch("generator.deepseek_client.time.sleep"):
            with self.assertRaises(RuntimeError):
                client.chat_json("m", "s", "p", stage="topics")

        usage = client.usage.to_dict()
        self.assertEqual(usage["failed_calls"], 1)
        self.assertEqual(usage["calls"], 0)
        self.assertEqual(usage["retries"], 1, "one retry was consumed")
        self.assertIn("topics", usage["by_stage"])

    def test_reported_attempts_match_the_real_request_count(self) -> None:
        client = _client(max_retries=1)
        calls = {"n": 0}

        def boom(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            raise httpx.ConnectError("down")

        with patch.object(client.client.chat.completions, "create", side_effect=boom), \
             patch("generator.deepseek_client.time.sleep"):
            with self.assertRaises(RuntimeError):
                client.chat_json("m", "s", "p")

        self.assertEqual(client.last_used_attempts, calls["n"])

    def test_usage_handles_a_response_without_usage(self) -> None:
        client = _client()
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
        with patch.object(client.client.chat.completions, "create", return_value=response):
            client.chat_json("m", "s", "p", stage="digest")

        self.assertEqual(client.usage.to_dict()["total_tokens"], 0)
        self.assertEqual(client.usage.to_dict()["calls"], 1)


class TokenUsageUnitTests(unittest.TestCase):
    def test_totals_and_stage_keys_are_serialisable(self) -> None:
        usage = TokenUsage()
        usage.record("a", SimpleNamespace(prompt_tokens=10, completion_tokens=5), 1.5)
        usage.record("b", None, 0.5, retries=2, failed=True)

        payload = usage.to_dict()

        self.assertEqual(payload["prompt_tokens"], 10)
        self.assertEqual(payload["failed_calls"], 1)
        self.assertEqual(payload["retries"], 2)
        self.assertEqual(sorted(payload["by_stage"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
