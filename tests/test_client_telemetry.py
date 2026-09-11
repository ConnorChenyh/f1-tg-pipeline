from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx

from analyzer.net import RetryPolicy
from generator.deepseek_client import (
    DeepSeekClient,
    RunDeadlineExceeded,
    TokenUsage,
    _is_retryable,
)


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


class RunDeadlineTests(unittest.TestCase):
    """N3: the whole run needs a ceiling, not just each request."""

    def _client_with(self, max_total: float) -> DeepSeekClient:
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            return DeepSeekClient(
                {
                    "deepseek": {
                        "max_retries": 3,
                        "force_json_object": False,
                        "max_total_seconds": max_total,
                    }
                }
            )

    def test_zero_disables_the_budget(self) -> None:
        client = self._client_with(0)
        self.assertIsNone(client.deadline_remaining())

    def test_a_positive_budget_reports_remaining_time(self) -> None:
        client = self._client_with(60)
        remaining = client.deadline_remaining()
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 60)

    def test_an_exhausted_budget_aborts_without_calling_the_model(self) -> None:
        client = self._client_with(60)
        client._deadline = 0.0  # pretend the budget is spent
        calls = {"n": 0}

        def spy(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            return _ok()

        with patch.object(client.client.chat.completions, "create", side_effect=spy):
            with self.assertRaises(RunDeadlineExceeded):
                client.chat_json("m", "s", "p", stage="digest")

        self.assertEqual(calls["n"], 0, "no request may be attempted once the budget is spent")
        self.assertEqual(client.usage.to_dict()["failed_calls"], 1)

    def test_retry_is_skipped_when_it_would_exceed_the_budget(self) -> None:
        client = self._client_with(60)
        client.retry_policy = RetryPolicy(attempts=5, backoff_sec=30.0, max_backoff_sec=30.0)
        client._deadline = time.monotonic() + 1.0  # only a moment left
        calls = {"n": 0}
        slept: list[float] = []

        def boom(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            raise httpx.ConnectError("down")

        with patch.object(client.client.chat.completions, "create", side_effect=boom), \
             patch("generator.deepseek_client.time.sleep", side_effect=slept.append):
            with self.assertRaises(RuntimeError):
                client.chat_json("m", "s", "p")

        self.assertEqual(calls["n"], 1, "the retry must not be attempted")
        self.assertEqual(slept, [], "must not sleep past the deadline")

    def test_a_spent_budget_does_not_block_a_fresh_run(self) -> None:
        client = self._client_with(60)
        with patch.object(client.client.chat.completions, "create", return_value=_ok()):
            result = client.chat_json("m", "s", "p", stage="topics")
        self.assertEqual(result, {"ok": True})


class DeadlineIntegrationTests(unittest.TestCase):
    """An exhausted budget must fail the run cleanly, not traceback."""

    def test_topic_extraction_deadline_fails_the_run(self) -> None:
        from tests.harness import RunHarness, _base_config, _stub_post
        import run as run_module
        from datetime import datetime, timezone
        import tempfile
        from pathlib import Path

        harness = RunHarness()
        scripts = harness._scripts()
        client = harness._fake_client(scripts, [])

        def expired(*a: Any, **k: Any) -> Any:
            raise RunDeadlineExceeded("budget spent")

        client.chat_json = expired

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=client), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                code = run_module.main()

        self.assertEqual(code, 1, "an exhausted budget must be a clean failure")

    def test_digest_deadline_fails_the_run_cleanly(self) -> None:
        from tests.harness import RunHarness, _base_config, _stub_post, _stub_topic
        import run as run_module
        from datetime import datetime, timezone
        import tempfile
        from pathlib import Path

        harness = RunHarness()
        scripts = harness._scripts()
        client = harness._fake_client(scripts, [])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=client), \
                 patch.object(run_module, "extract_topics", return_value=[_stub_topic()]), \
                 patch.object(
                     run_module, "generate_digest", side_effect=RunDeadlineExceeded("budget spent")
                 ), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                code = run_module.main()

        self.assertEqual(code, 1, "a spent budget during writing must fail cleanly")
