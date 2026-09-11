"""Reproductions for the P1 findings raised in section 9 of the review report.

These tests assert the *current* (buggy) behaviour so the failures are visible.
Each is rewritten to assert correct behaviour as the corresponding fix lands.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import run as run_module
from analyzer.run_state import (
    STAGE_COLLECT,
    find_resumable_run,
    STAGE_DELIVERED,
    STAGE_DIGEST,
    STAGE_TOPICS,
    RunState,
    load_run_state,
    mark_active_run,
    save_run_state,
)
from tests.harness import RunHarness
from tests.test_run_state import _base_config, _stub_post, _stub_topic


class R1ResumeAfterTopicsTests(unittest.TestCase):
    """R1: resuming when collect+topics are done but the digest is not."""

    def test_resume_from_topics_checkpoint_can_still_write(self) -> None:
        helper = RunHarness()
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
        helper = RunHarness()
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
        helper = RunHarness()
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
        helper = RunHarness()
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


class R9TestModeCompensationTests(unittest.TestCase):
    """R9 (9.4 boundary): test modes must not trigger a real compensation send."""

    def test_mock_run_does_not_compensate_the_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            (root / "output" / "pending_telegram_deliveries.json").write_text(
                json.dumps({"deliveries": [{"output_dir": "output/2026-01-01_000000", "queued_at": "x"}]}),
                encoding="utf-8",
            )
            sent: list = []

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "deliver_pending_digests", side_effect=lambda *a, **k: sent.append("compensate") or 1), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module.sys, "argv", ["run.py", "--mock", "--push-telegram"]):
                run_module.main()

        self.assertEqual(sent, [], "a --mock run must never send queued Telegram digests")


class B1MockQueuePollutionTests(unittest.TestCase):
    """B1: a mock delivery failure must not pollute the shared compensation queue."""

    def test_mock_delivery_failure_does_not_queue(self) -> None:
        helper = RunHarness()
        scripts = helper._scripts()
        client = helper._fake_client(scripts, [])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            def boom(*a, **k):
                raise RuntimeError("telegram down")

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
                 patch.object(run_module, "push_digest_to_telegram", side_effect=boom), \
                 patch.object(run_module.sys, "argv", ["run.py", "--mock", "--push-telegram"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                run_module.main()

            queue = root / "output" / "pending_telegram_deliveries.json"
            queued = json.loads(queue.read_text(encoding="utf-8")) if queue.exists() else {"deliveries": []}

        self.assertEqual(
            queued.get("deliveries"),
            [],
            "a mock run must not queue a test digest for real compensation",
        )


class B4ResumeWithoutApiKeyTests(unittest.TestCase):
    """B4: resuming a run whose digest is already written needs no model key."""

    def test_resume_without_api_key_after_digest_checkpoint(self) -> None:
        helper = RunHarness()
        scripts = helper._scripts()
        draft = scripts["digest"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            run_dir = root / "output" / "2026-09-11_120000"
            draft_dir = run_dir / "drafts" / "digest"
            draft_dir.mkdir(parents=True)
            (run_dir / "shortlisted_posts.json").write_text(
                json.dumps([_stub_post(now).to_dict()]), encoding="utf-8"
            )
            (run_dir / "topics.json").write_text(json.dumps([_stub_topic()]), encoding="utf-8")
            (draft_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            save_run_state(
                run_dir,
                RunState(run_dir.name, now.isoformat(), 24, [STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST]),
            )
            mark_active_run(root, run_dir)

            env = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}
            with patch.dict("os.environ", env, clear=False), \
                 patch.dict("os.environ", {}, clear=False), \
                 patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24", "--resume"]):
                from analyzer.context import RunContext as RealRunContext

                ctx_cls.now.return_value = RealRunContext.now(24)
                with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
                    os.environ.pop("DEEPSEEK_API_KEY", None)
                    code = run_module.main()

        self.assertEqual(code, 0, "rendering an already-written draft must not need an API key")


class B2CompensationThenFailureTests(unittest.TestCase):
    """B2: a local failure after compensation must not cause a second send."""

    def _prepare(self, root: Path, now: datetime, draft: dict) -> Path:
        run_dir = root / "output" / "2026-09-11_120000"
        draft_dir = run_dir / "drafts" / "digest"
        draft_dir.mkdir(parents=True)
        (run_dir / "shortlisted_posts.json").write_text(
            json.dumps([_stub_post(now).to_dict()]), encoding="utf-8"
        )
        (run_dir / "topics.json").write_text(json.dumps([_stub_topic()]), encoding="utf-8")
        (draft_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        save_run_state(
            run_dir,
            RunState(run_dir.name, now.isoformat(), 24, [STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST]),
        )
        mark_active_run(root, run_dir)
        (root / "output" / "pending_telegram_deliveries.json").write_text(
            json.dumps({"deliveries": [{"output_dir": f"output/{run_dir.name}", "queued_at": now.isoformat()}]}),
            encoding="utf-8",
        )
        return run_dir

    def test_failure_after_compensation_does_not_resend_on_resume(self) -> None:
        helper = RunHarness()
        scripts = helper._scripts()
        client = helper._fake_client(scripts, [])
        draft = scripts["digest"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)
            run_dir = self._prepare(root, now, draft)
            deliveries: list = []

            def compensate(*a, **k):
                deliveries.append("compensate")
                (root / "output" / "pending_telegram_deliveries.json").write_text(
                    json.dumps({"deliveries": []}), encoding="utf-8"
                )
                return 1

            def render_boom(*a, **k):
                raise OSError("disk full")


            from analyzer.context import RunContext as RealRunContext
            from contextlib import ExitStack

            def invoke(extra_patches: list, argv: list[str]) -> int:  # noqa: ANN001
                with ExitStack() as stack:
                    stack.enter_context(patch.object(run_module, "ROOT", root))
                    stack.enter_context(patch.object(run_module, "load_config", return_value=config))
                    stack.enter_context(
                        patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False)
                    )
                    stack.enter_context(patch.object(run_module, "build_season_context_prompt", return_value=""))
                    stack.enter_context(patch.object(run_module, "build_season_snapshot", return_value={}))
                    stack.enter_context(patch.object(run_module, "load_season_snapshot", return_value=None))
                    stack.enter_context(patch.object(run_module, "build_season_update_message", return_value=None))
                    stack.enter_context(patch.object(run_module, "DeepSeekClient", return_value=client))
                    stack.enter_context(patch.object(run_module, "deliver_pending_digests", side_effect=compensate))
                    stack.enter_context(patch.object(run_module, "collect_reddit", return_value=[]))
                    stack.enter_context(patch.object(run_module, "collect_rss", return_value=[]))
                    stack.enter_context(patch.object(run_module, "collect_twitter", return_value=[]))
                    stack.enter_context(
                        patch.object(run_module, "build_output_dir", return_value=root / "output" / "fresh")
                    )
                    ctx = stack.enter_context(patch.object(run_module, "RunContext"))
                    ctx.now.return_value = RealRunContext.now(24)
                    for extra in extra_patches:
                        stack.enter_context(extra)
                    stack.enter_context(patch.object(run_module.sys, "argv", argv))
                    return run_module.main()

            # Pass 1: compensation succeeds, then rendering fails.
            first = invoke(
                [patch.object(run_module, "generate_images_for_digest", side_effect=render_boom)],
                ["run.py", "--hours", "24", "--resume", "--push-telegram"],
            )
            self.assertNotEqual(first, 0, "the render failure must surface as a failure")

            # The delivery fact must already be on disk: the queue is empty, so
            # an in-memory-only record would be gone by the next process.
            state_after_failure = load_run_state(run_dir)
            self.assertTrue(
                state_after_failure.has(STAGE_DELIVERED),
                "delivery must be committed before the render step can fail",
            )

            # The persisted mark makes this run non-resumable. That is the whole
            # defence: the queue is already empty, so an in-memory-only record
            # would leave nothing to stop a later process resending it.
            self.assertIsNone(
                find_resumable_run(root, datetime.now(timezone.utc), 6),
                "a compensated run must not be resumable again",
            )

        self.assertEqual(deliveries.count("compensate"), 1, "compensation runs once")
        self.assertNotIn("send", deliveries, "the digest was delivered by compensation only")
        self.assertNotIn("resend", deliveries, "no second delivery may ever happen")
