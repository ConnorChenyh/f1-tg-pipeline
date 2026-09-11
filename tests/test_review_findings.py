"""Reproductions for the P1 findings raised in section 9 of the review report.

These tests assert the *current* (buggy) behaviour so the failures are visible.
Each is rewritten to assert correct behaviour as the corresponding fix lands.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import run as run_module
from analyzer.run_state import (
    STAGE_COLLECT,
    STAGE_DIGEST,
    STAGE_TOPICS,
    RunState,
    mark_active_run,
    save_run_state,
)
from tests.test_resume import ResumeIntegrationTests
from tests.test_run_state import _base_config, _stub_post, _stub_topic


class R1ResumeAfterTopicsTests(unittest.TestCase):
    """R1: resuming when collect+topics are done but the digest is not."""

    def test_resume_from_topics_checkpoint_can_still_write(self) -> None:
        helper = ResumeIntegrationTests()
        scripts = helper._scripts()
        calls: list = []
        client = helper._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            # Craft a run that already finished collect + topics.
            run_dir = root / "output" / "2026-09-11_120000"
            run_dir.mkdir(parents=True)
            (run_dir / "shortlisted_posts.json").write_text(
                json.dumps([_stub_post(now).to_dict()]), encoding="utf-8"
            )
            (run_dir / "topics.json").write_text(
                json.dumps([_stub_topic()]), encoding="utf-8"
            )
            save_run_state(
                run_dir,
                RunState(run_dir.name, now.isoformat(), 24, [STAGE_COLLECT, STAGE_TOPICS]),
            )
            mark_active_run(root, run_dir)

            code = helper._invoke(
                root, config, client, ["run.py", "--hours", "24", "--resume"], now
            )

        self.assertEqual(code, 0, "resuming after the topics checkpoint must succeed")
        self.assertGreater(len(calls), 0, "the digest still needs an LLM call")


class R3GuardSeasonTests(unittest.TestCase):
    """R3: a guard-rejected digest must not send season updates or advance."""

    def test_guard_block_still_sends_season_update(self) -> None:
        helper = ResumeIntegrationTests()
        scripts = helper._scripts()
        client = helper._fake_client(scripts, [])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)
            sent: list = []
            snapshots: list = []

            draft = {
                "title": "围场过去24H新闻",
                "hook": "",
                "items": [{"ordinal": "一", "headline": "标题", "content": "正文"}],
                "hashtags": [],
                "sources": ["https://www.motorsport.com/f1/news/example"],
            }

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={"phase": "x"}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value="赛季更新"), \
                 patch.object(run_module, "DeepSeekClient", return_value=client), \
                 patch.object(run_module, "extract_topics", return_value=[_stub_topic()]), \
                 patch.object(run_module, "enrich_topics_with_evidence", side_effect=lambda t, _p: t), \
                 patch.object(run_module, "generate_digest", return_value=(draft, [], ["too_few_items"])), \
                 patch.object(run_module, "send_text_to_telegram", side_effect=lambda *a, **k: sent.append("season")), \
                 patch.object(run_module, "save_season_snapshot", side_effect=lambda *a, **k: snapshots.append("snap")), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24", "--push-telegram"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                code = run_module.main()

        self.assertEqual(code, 0)
        self.assertEqual(sent, [], "guard-rejected digest must not send the season update")
        self.assertEqual(snapshots, [], "guard-rejected digest must not advance the snapshot")


class R4CompensatedResumeTests(unittest.TestCase):
    """R4: resuming after a successful compensation must not resend the digest."""

    def test_resume_after_compensation_does_not_resend(self) -> None:
        helper = ResumeIntegrationTests()
        scripts = helper._scripts()
        client = helper._fake_client(scripts, [])
        draft = scripts["digest"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)
            deliveries: list = []

            # A previous run finished its draft but failed to deliver, so the
            # digest sits in the pending queue.
            run_dir = root / "output" / "2026-09-11_120000"
            draft_dir = run_dir / "drafts" / "digest"
            draft_dir.mkdir(parents=True)
            (run_dir / "shortlisted_posts.json").write_text(
                json.dumps([_stub_post(now).to_dict()]), encoding="utf-8"
            )
            (run_dir / "topics.json").write_text(json.dumps([_stub_topic()]), encoding="utf-8")
            (draft_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            (draft_dir / "images").mkdir()
            (draft_dir / "images" / "cover.png").write_bytes(b"png")
            save_run_state(
                run_dir,
                RunState(run_dir.name, now.isoformat(), 24, [STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST]),
            )
            mark_active_run(root, run_dir)
            (root / "output" / "pending_telegram_deliveries.json").write_text(
                json.dumps({"deliveries": [{"output_dir": f"output/{run_dir.name}", "queued_at": now.isoformat()}]}),
                encoding="utf-8",
            )

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=client), \
                 patch.object(run_module, "push_digest_to_telegram", side_effect=lambda *a, **k: deliveries.append("send")), \
                 patch.object(
                     run_module,
                     "deliver_pending_digests",
                     side_effect=lambda *a, **k: _compensate(root, run_dir, deliveries),
                 ), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24", "--resume", "--push-telegram"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                code = run_module.main()

        self.assertEqual(code, 0)
        self.assertEqual(deliveries, ["compensate"], "the digest is delivered exactly once")
        self.assertNotIn("resend", deliveries, "resume must not deliver the same digest twice")


def _compensate(root: Path, run_dir: Path, deliveries: list) -> int:
    """Simulate a successful compensation pass clearing the queue."""
    deliveries.append("compensate")
    (root / "output" / "pending_telegram_deliveries.json").write_text(
        json.dumps({"deliveries": []}), encoding="utf-8"
    )
    return 1


class R5TelegramDryRunStateTests(unittest.TestCase):
    """R5: --telegram-dry-run must not persist published state."""

    def test_telegram_dry_run_does_not_write_history(self) -> None:
        helper = ResumeIntegrationTests()
        scripts = helper._scripts()
        client = helper._fake_client(scripts, [])

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
                 patch.object(run_module, "push_digest_to_telegram", return_value={"dry_run": True}), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24", "--push-telegram", "--telegram-dry-run"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                code = run_module.main()

            self.assertEqual(code, 0)
            history_path = root / "output" / "topic_history.json"
            history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
            self.assertEqual(history, [], "a Telegram dry run must not record published topics")


if __name__ == "__main__":
    unittest.main()
