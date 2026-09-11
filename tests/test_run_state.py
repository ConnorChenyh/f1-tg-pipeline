from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run as run_module
from generator.images import RENDER_MEASUREMENTS_FILENAME

from tests.harness import (  # noqa: E402
    MISSING,
    STATE_FILES,
    _base_config,
    _changed_state,
    _hashes,
    _published_state,
    _stub_post,
    _stub_topic,
)


class RunStateIsolationTests(unittest.TestCase):
    def test_dry_run_does_not_write_published_topic_memory(self) -> None:
        """A collect-only run must not touch story memory (P0-1 regression)."""
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            before = _hashes(root)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module.sys, "argv", ["run.py", "--dry-run", "--hours", "24"]):
                run_context_cls.now.return_value = type(
                    "Ctx",
                    (),
                    {
                        "generated_at": now,
                        "window_hours": 24,
                        "f1_season": 2026,
                        "with_season_context": lambda self, _value: self,
                    },
                )()
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0)
            history, published = _published_state(root)
            self.assertEqual(history, [], "dry run recorded topic history")
            self.assertEqual(published, 0, "dry run recorded published topics")
            self.assertEqual(_changed_state(root, before), [], "dry run touched persistent state")
            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")

    def test_mock_run_leaves_published_memory_untouched(self) -> None:
        """--mock builds a digest but must not claim anything was published."""
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            before = _hashes(root)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module.sys, "argv", ["run.py", "--mock", "--hours", "24"]):
                run_context_cls.now.return_value = type(
                    "Ctx",
                    (),
                    {
                        "generated_at": now,
                        "window_hours": 24,
                        "f1_season": 2026,
                        "with_season_context": lambda self, _value: self,
                    },
                )()
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0)
            history, published = _published_state(root)
            self.assertEqual(history, [], "mock run recorded topic history")
            self.assertEqual(published, 0, "mock run recorded published topics")
            self.assertEqual(
                _changed_state(root, before),
                [],
                "mock run touched persistent state (season snapshot or story memory)",
            )
            # The digest itself must still be produced.
            self.assertTrue((root / "output" / "run1" / "drafts" / "digest" / "draft.json").exists())
            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")


class GuardDegradationTests(unittest.TestCase):
    def test_blocked_draft_is_saved_and_delivery_skipped(self) -> None:
        """A quality-guard rejection must not discard the whole run (P1-1)."""
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            draft = {
                "title": "围场过去24H新闻",
                "hook": "",
                "items": [{"ordinal": "一", "headline": "标题", "content": "正文"}],
                "hashtags": [],
                "sources": ["https://www.motorsport.com/f1/news/example"],
            }
            pushed: list[str] = []

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=object()), \
                 patch.object(run_module, "extract_topics", return_value=[_stub_topic()]), \
                 patch.object(run_module, "enrich_topics_with_evidence", side_effect=lambda topics, _posts: topics), \
                 patch.object(run_module, "generate_digest", return_value=(draft, ["质量检查提示"], ["too_few_items"])), \
                 patch.object(run_module, "push_digest_to_telegram", side_effect=lambda *a, **k: pushed.append("push")), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24", "--push-telegram"]):
                run_context_cls.now.return_value = type(
                    "Ctx",
                    (),
                    {
                        "generated_at": now,
                        "window_hours": 24,
                        "f1_season": 2026,
                        "with_season_context": lambda self, _value: self,
                    },
                )()
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0, "a guard rejection must not fail the run")
            self.assertEqual(pushed, [], "a rejected draft must not be delivered")

            draft_dir = root / "output" / "run1" / "drafts" / "digest"
            self.assertTrue((draft_dir / "draft.json").exists(), "draft must be saved for review")

            meta = json.loads((draft_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertTrue(meta["guard_blocked"])
            self.assertEqual(meta["guard_blocking_codes"], ["too_few_items"])

            # Topics still enter history so the rejected story is not re-picked
            # and re-rejected on the next run.
            history = json.loads((root / "output" / "topic_history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history), 1)
            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")


class GenerateDigestGuardTests(unittest.TestCase):
    """Exercise the real generate_digest, not a mocked return value."""

    def _client(self, digest_payload: dict):
        from generator.deepseek_client import DeepSeekClient

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})

        payloads = [json.dumps(digest_payload)]
        client.chat_json = lambda model, system, user, validator=None, stage="unknown": json.loads(payloads.pop(0))
        return client

    def _run(self, draft: dict):
        from generator.digest_writer import generate_digest

        topics = [_stub_topic()]
        client = self._client(draft)
        ctx = SimpleNamespace(to_prompt_block=lambda: "ctx", generated_at=datetime(2026, 9, 11, tzinfo=timezone.utc))
        return generate_digest(
            client,
            topics,
            ctx,
            digest_title="围场过去24H新闻",
            min_items=3,
            max_items=5,
            fact_check_enabled=False,
            final_review_enabled=False,
        )

    def test_blocking_guard_returns_draft_instead_of_raising(self) -> None:
        draft = {
            "items": [{"ordinal": "一", "headline": "标题", "content": "正文"}],
            "sources": ["https://www.motorsport.com/f1/news/example"],
        }

        result_draft, notes, blocking = self._run(draft)

        self.assertEqual(blocking, ["too_few_items"])
        self.assertEqual(result_draft["title"], "围场过去24H新闻")
        self.assertTrue(any("质量检查提示" in note for note in notes))

    def test_clean_draft_reports_no_blockers(self) -> None:
        draft = {
            "items": [
                {"ordinal": "一", "headline": "标题一", "content": "正文一"},
                {"ordinal": "二", "headline": "标题二", "content": "正文二"},
                {"ordinal": "三", "headline": "标题三", "content": "正文三"},
            ],
            "sources": ["https://www.motorsport.com/f1/news/example"],
        }

        _, _, blocking = self._run(draft)

        self.assertEqual(blocking, [])


if __name__ == "__main__":
    unittest.main()


class FullPathWithStubLLMTests(unittest.TestCase):
    """Run the real non-mock pipeline with only the HTTP layer replaced.

    This exercises analyze.topics, generator.digest_writer, quality_guard and
    image generation together: the wiring the unit tests each stub out.
    """

    def _scripts(self) -> dict:
        return {
            "topics": {
                "topics": [
                    {
                        "id": "topic_01",
                        "title_zh": "法拉利西班牙站底板升级",
                        "summary": "法拉利带来新底板以改善低速弯表现。",
                        "heat_score": 82,
                        "evidence_urls": ["https://www.motorsport.com/f1/news/example"],
                        "publish_recommendation": "publish",
                        "skip_reason": None,
                    }
                ]
            },
            "digest": {
                "title": "围场过去24H新闻",
                "hook": "过去24小时，围场有这些值得关注的消息：",
                "items": [
                    {
                        "ordinal": "一",
                        "headline": "法拉利底板升级",
                        "content": "法拉利在西班牙站带来修订版底板，目标是改善低速弯的下压力表现。" * 6,
                    }
                ],
                "hashtags": ["#F1"],
                "sources": ["https://www.motorsport.com/f1/news/example"],
                "risk_note": "无",
            },
        }

    def test_real_modules_end_to_end(self) -> None:
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        scripts = self._scripts()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

            prompts: list[str] = []

            def fake_chat_json(model, system, user, validator=None, stage="unknown"):
                prompts.append(user)
                payload = scripts["topics"] if '"topics"' in user else scripts["digest"]
                return validator(json.loads(json.dumps(payload))) if validator else payload

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
                from generator.deepseek_client import DeepSeekClient

                stub_client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})
            stub_client.chat_json = fake_chat_json

            from analyzer.context import RunContext as RealRunContext

            real_context = RealRunContext.now(24)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=stub_client), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24"]):
                run_context_cls.now.return_value = real_context
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0, "real module path must complete")
            self.assertTrue(prompts, "the LLM layer was never called")

            draft_dir = root / "output" / "run1" / "drafts" / "digest"
            meta = json.loads((draft_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertFalse(meta["guard_blocked"], f"guard rejected: {meta['guard_blocking_codes']}")
            self.assertEqual(meta["digest_title"], "围场过去24H新闻")

            draft = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["items"][0]["headline"], "法拉利底板升级")

            # Images plus their layout report were produced.
            self.assertTrue((draft_dir / "images" / "cover.png").exists())
            self.assertTrue((draft_dir / "images" / "slide_01.png").exists())
            measurements = json.loads(
                (draft_dir / RENDER_MEASUREMENTS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(len(measurements["items"]), 1)

            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")


class TelemetryInMetaTests(unittest.TestCase):
    """N2: a run must record what the model calls cost and how long they took."""

    def test_model_usage_lands_in_meta_json(self) -> None:
        helper = RunHarness()
        scripts = helper._scripts()
        calls: list = []
        client = helper._fake_client(scripts, calls)

        # Attach a usage record the way a real client would.
        from generator.deepseek_client import TokenUsage

        client.usage = TokenUsage()
        client.usage.begin_call("topics")
        client.usage.record("topics", SimpleNamespace(prompt_tokens=900, completion_tokens=150), 2.5)
        client.usage.begin_call("digest")
        client.usage.record("digest", SimpleNamespace(prompt_tokens=3000, completion_tokens=600), 7.5)

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

            meta = json.loads(
                (root / "output" / "run1" / "drafts" / "digest" / "meta.json").read_text(encoding="utf-8")
            )

        self.assertEqual(code, 0)
        self.assertIn("model_usage", meta, "meta.json must carry the run's model cost")
        usage = meta["model_usage"]
        self.assertEqual(usage["total_tokens"], 4650)
        self.assertEqual(usage["logical_calls"], 2)
        self.assertEqual(usage["requests"], 2)
        self.assertEqual(sorted(usage["by_stage"]), ["digest", "topics"])
        self.assertGreater(usage["request_latency_sec"], 0)

    def test_persisted_usage_is_merged_with_a_resumed_client(self) -> None:
        previous = {
            "logical_calls": 1,
            "requests": 2,
            "failed_requests": 1,
            "retries": 1,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "request_latency_sec": 2.0,
            "by_stage": {"topics": {"requests": 2, "failed_requests": 1}},
        }
        current = {
            "logical_calls": 1,
            "requests": 1,
            "failed_requests": 0,
            "retries": 0,
            "prompt_tokens": 300,
            "completion_tokens": 60,
            "total_tokens": 360,
            "request_latency_sec": 3.0,
            "by_stage": {"digest": {"requests": 1, "failed_requests": 0}},
        }

        merged = run_module._merge_model_usage(previous, current)

        self.assertEqual(merged["logical_calls"], 2)
        self.assertEqual(merged["requests"], 3)
        self.assertEqual(merged["failed_requests"], 1)
        self.assertEqual(merged["total_tokens"], 480)
        self.assertEqual(sorted(merged["by_stage"]), ["digest", "topics"])

    def test_failure_usage_is_written_without_discarding_existing_meta(self) -> None:
        from generator.deepseek_client import TokenUsage

        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts" / "digest"
            draft_dir.mkdir(parents=True)
            (draft_dir / "meta.json").write_text(
                json.dumps({"guard_blocked": False, "model_usage": {"requests": 1}}),
                encoding="utf-8",
            )
            client = SimpleNamespace(usage=TokenUsage())
            client.usage.begin_call("digest")
            client.usage.record("digest", None, 0.5, failed=True)

            run_module._persist_model_usage(draft_dir, client)

            meta = json.loads((draft_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertFalse(meta["guard_blocked"])
            self.assertEqual(meta["model_usage"]["requests"], 2)
            self.assertEqual(meta["model_usage"]["failed_requests"], 1)


from types import SimpleNamespace  # noqa: E402  (used by the test above)
from tests.harness import RunHarness  # noqa: E402
