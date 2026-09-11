from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import run as run_module
from analyzer.run_state import (
    STAGE_COLLECT,
    STAGE_DIGEST,
    STAGE_DELIVERED,
    STAGE_IMAGES,
    STAGE_TOPICS,
    RunState,
    find_resumable_run,
    load_run_state,
    mark_active_run,
    save_run_state,
)
from tests.test_run_state import _base_config, _stub_post


def _write_state(root: Path, dirname: str, state: RunState) -> Path:
    output_dir = root / "output" / dirname
    output_dir.mkdir(parents=True, exist_ok=True)
    save_run_state(output_dir, state)
    return output_dir


class FindResumableRunTests(unittest.TestCase):
    def test_returns_none_without_an_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertIsNone(
                find_resumable_run(root, datetime(2026, 9, 11, 12, tzinfo=timezone.utc))
            )

    def test_returns_the_unfinished_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            started = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            _write_state(
                root,
                "2026-09-11_120000",
                RunState("2026-09-11_120000", started.isoformat(), 24, [STAGE_COLLECT]),
            )
            mark_active_run(root, root / "output" / "2026-09-11_120000")

            found = find_resumable_run(root, started + timedelta(minutes=10))

        self.assertIsNotNone(found)
        output_dir, state = found
        self.assertEqual(output_dir.name, "2026-09-11_120000")
        self.assertTrue(state.has(STAGE_COLLECT))

    def test_refuses_a_run_older_than_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            started = datetime(2026, 9, 11, 2, 0, tzinfo=timezone.utc)
            _write_state(
                root,
                "2026-09-11_020000",
                RunState("2026-09-11_020000", started.isoformat(), 24, [STAGE_COLLECT]),
            )
            mark_active_run(root, root / "output" / "2026-09-11_020000")

            found = find_resumable_run(root, started + timedelta(hours=9), max_age_hours=6)

        self.assertIsNone(found, "a stale window must never be published")

    def test_refuses_a_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            started = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            _write_state(
                root,
                "2026-09-11_120000",
                RunState(
                    "2026-09-11_120000",
                    started.isoformat(),
                    24,
                    [STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST, STAGE_IMAGES, STAGE_DELIVERED],
                ),
            )
            mark_active_run(root, root / "output" / "2026-09-11_120000")

            found = find_resumable_run(root, started + timedelta(minutes=5))

        self.assertIsNone(found)

    def test_ignores_a_corrupt_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            (root / "output" / "active_run.json").write_text("{broken", encoding="utf-8")

            self.assertIsNone(
                find_resumable_run(root, datetime(2026, 9, 11, 12, tzinfo=timezone.utc))
            )

    def test_round_trips_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            state = RunState("r1", "2026-09-11T12:00:00+00:00", 24, [STAGE_COLLECT])
            save_run_state(output_dir, state)

            loaded = load_run_state(output_dir)

        self.assertEqual(loaded.run_id, "r1")
        self.assertEqual(loaded.window_hours, 24)
        self.assertEqual(loaded.completed, [STAGE_COLLECT])


class ResumeIntegrationTests(unittest.TestCase):
    """Drive run.py twice and prove the second pass does not redo paid work."""

    def _fake_client(self, scripts: dict, calls: list) -> object:
        def fake_chat_json(model, system, user, validator=None):
            calls.append(user)
            payload = scripts["topics"] if '"topics"' in user else scripts["digest"]
            return validator(json.loads(json.dumps(payload))) if validator else payload

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            from generator.deepseek_client import DeepSeekClient

            client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})
        client.chat_json = fake_chat_json
        return client

    def _scripts(self) -> dict:
        return {
            "topics": {
                "topics": [
                    {
                        "id": "topic_01",
                        "title_zh": "法拉利西班牙站底板升级",
                        "summary": "法拉利带来新底板。",
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
                        "content": "法拉利在西班牙站带来修订版底板。" * 20,
                    }
                ],
                "hashtags": ["#F1"],
                "sources": ["https://www.motorsport.com/f1/news/example"],
                "risk_note": "无",
            },
        }

    def _invoke(self, root: Path, config: dict, client, argv: list[str], now: datetime) -> int:
        from analyzer.context import RunContext as RealRunContext

        collector_posts = [_stub_post(now)]
        with patch.object(run_module, "ROOT", root), \
             patch.object(run_module, "load_config", return_value=config), \
             patch.object(run_module, "collect_reddit", return_value=[]), \
             patch.object(run_module, "collect_rss", return_value=collector_posts), \
             patch.object(run_module, "collect_twitter", return_value=[]), \
             patch.object(run_module, "RunContext") as run_context_cls, \
             patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
             patch.object(run_module, "build_season_context_prompt", return_value=""), \
             patch.object(run_module, "build_season_snapshot", return_value={}), \
             patch.object(run_module, "load_season_snapshot", return_value=None), \
             patch.object(run_module, "build_season_update_message", return_value=None), \
             patch.object(run_module, "DeepSeekClient", return_value=client), \
             patch.object(run_module.sys, "argv", argv):
            run_context_cls.now.return_value = RealRunContext.now(24)
            return run_module.main()

    def test_resume_reuses_draft_without_calling_the_llm_again(self) -> None:
        scripts = self._scripts()
        calls: list = []
        client = self._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            first = self._invoke(root, config, client, ["run.py", "--hours", "24"], now)
            self.assertEqual(first, 0)
            self.assertGreater(len(calls), 0, "first pass must call the LLM")
            calls_after_first = len(calls)

            # Simulate a crash before delivery by un-marking the last stages.
            runs = [p for p in (root / "output").iterdir() if p.is_dir()]
            self.assertEqual(len(runs), 1)
            run_dir = runs[0]
            state = load_run_state(run_dir)
            self.assertTrue(state.has(STAGE_DIGEST))

            # Rebuild state as if the run died right after writing the draft.
            crashed = RunState(
                state.run_id,
                state.generated_at,
                state.window_hours,
                [STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST],
            )
            save_run_state(run_dir, crashed)

            second = self._invoke(
                root, config, client, ["run.py", "--hours", "24", "--resume"], datetime.now(timezone.utc)
            )

            self.assertEqual(second, 0)
            self.assertEqual(
                len(calls),
                calls_after_first,
                "resume must not call the LLM again for an already-written draft",
            )

            resumed_state = load_run_state(run_dir)
            self.assertTrue(resumed_state.has(STAGE_IMAGES))
            self.assertTrue(resumed_state.has(STAGE_DELIVERED))

    def test_resume_with_nothing_to_resume_starts_fresh(self) -> None:
        scripts = self._scripts()
        calls: list = []
        client = self._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)

            code = self._invoke(
                root, config, client, ["run.py", "--hours", "24", "--resume"], datetime.now(timezone.utc)
            )

            self.assertEqual(code, 0)
            self.assertGreater(len(calls), 0, "a fresh run still calls the LLM")

    def test_resume_is_ignored_for_mock_runs(self) -> None:
        scripts = self._scripts()
        calls: list = []
        client = self._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)

            code = self._invoke(
                root,
                config,
                client,
                ["run.py", "--hours", "24", "--mock", "--resume"],
                datetime.now(timezone.utc),
            )

            self.assertEqual(code, 0)
            self.assertEqual(calls, [], "mock must never call the LLM")
            # A mock run writes no resumable pointer.
            self.assertFalse((root / "output" / "active_run.json").exists())


if __name__ == "__main__":
    unittest.main()
