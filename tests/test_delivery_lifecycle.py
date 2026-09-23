"""Integration checks for publication state without external sends or models."""
from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import run
import scheduler
from analyzer.run_state import STAGE_DELIVERED, load_run_state, find_resumable_run
from publisher.telegram_delivery_queue import deliver_pending_digests, enqueue_pending_delivery
from tests.harness import RunHarness, _base_config, _published_state


class DeliveryLifecycleTests(unittest.TestCase, RunHarness):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = _base_config(self.root)
        self.calls = []
        self.scripts = self._scripts()
        self.client = self._fake_client(self.scripts, self.calls)

    def invoke(self, *args):
        return self._invoke(self.root, self.config, self.client, ["run.py", *args], datetime.now(timezone.utc))

    def directory(self):
        return next(path for path in (self.root / "output").iterdir() if path.is_dir())

    def test_generation_without_send_is_not_published_or_resumable(self):
        self.assertEqual(self.invoke(), 0)
        state = load_run_state(self.directory())
        self.assertEqual(state.outcome, "generated")
        self.assertFalse(state.has(STAGE_DELIVERED))
        history, count = _published_state(self.root)
        self.assertEqual(count, 0)
        self.assertEqual(history[0]["outcome"], "generated")
        self.assertIsNone(find_resumable_run(self.root, datetime.now(timezone.utc)))

    def test_compensation_commits_published_memory_once(self):
        with patch.object(run, "push_digest_to_telegram", side_effect=RuntimeError("offline")):
            self.assertEqual(self.invoke("--push-telegram"), 1)
        directory = self.directory()
        self.assertEqual(load_run_state(directory).outcome, "delivery_pending")
        self.assertEqual(_published_state(self.root)[1], 0)
        send = MagicMock(return_value={"image_count": 2})
        with patch.object(run, "ROOT", self.root):
            for _ in range(2):
                deliver_pending_digests(self.root, self.config, send=send,
                    on_delivered=lambda path: run._commit_compensated_delivery(path, self.config),
                    already_delivered=run._delivery_is_committed)
            # Also prove that a repeated local finalization cannot duplicate rows.
            run._commit_compensated_delivery(directory, self.config)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(load_run_state(directory).has(STAGE_DELIVERED))
        self.assertEqual(_published_state(self.root)[1], 1)
        self.assertEqual(len(_published_state(self.root)[0]), 1)

    def test_real_renderer_truncation_blocks_delivery_and_publication(self):
        self.scripts["digest"]["items"][0]["content"] = "证据支持的完整正文。" * 400
        with patch.object(run, "push_digest_to_telegram") as send:
            self.assertEqual(self.invoke("--push-telegram"), 0)
            send.assert_not_called()
        directory = self.directory()
        meta = json.loads((directory / "drafts/digest/meta.json").read_text())
        self.assertIn("image_truncated", meta["guard_blocking_codes"])
        self.assertEqual(load_run_state(directory).outcome, "rejected")
        self.assertEqual(_published_state(self.root)[1], 0)

    def test_mock_push_validates_only(self):
        with patch.object(run, "push_digest_to_telegram", return_value={"dry_run": True}) as send:
            self.assertEqual(self.invoke("--mock", "--push-telegram"), 0)
            self.assertTrue(send.call_args.kwargs["dry_run"])
        self.assertEqual(_published_state(self.root)[1], 0)
        self.assertEqual(load_run_state(self.directory()).outcome, "generated")

    def test_resume_keeps_season_snapshot_and_original_provenance(self):
        self.config["season_context"]["marker"] = "original"
        with patch.object(run, "generate_images_for_digest", side_effect=RuntimeError("renderer unavailable")):
            self.assertEqual(self.invoke(), 1)
        directory = self.directory()
        provenance = json.loads((directory / "drafts/digest/meta.json").read_text())["provenance"]
        calls = len(self.calls)
        self.config["season_context"]["marker"] = "changed"
        self.assertEqual(self.invoke("--resume"), 0)
        self.assertEqual(self.config["season_context"]["marker"], "original")
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(json.loads((directory / "drafts/digest/meta.json").read_text())["provenance"], provenance)

    def test_corrupt_queue_is_not_overwritten_by_enqueue(self):
        queue = self.root / "output/pending_telegram_deliveries.json"
        queue.parent.mkdir()
        queue.write_text('{"deliveries":')
        with self.assertRaises(ValueError):
            enqueue_pending_delivery(self.root, self.config, self.root / "output/2026-09-23_120000")
        self.assertEqual(queue.read_text(), '{"deliveries":')


class SchedulerDeadlineTests(unittest.TestCase):
    def test_deadline_terminates_entire_process_group(self):
        process = MagicMock(pid=12345)
        process.__enter__.return_value = process
        process.wait.side_effect = [subprocess.TimeoutExpired("pipeline", 5), 0]
        with patch.object(scheduler.subprocess, "Popen", return_value=process) as start, \
             patch.object(scheduler.os, "killpg") as kill:
            self.assertEqual(scheduler.run_pipeline(Path("config.yaml"), 24, True, 5), 124)
        self.assertTrue(start.call_args.kwargs["start_new_session"])
        kill.assert_called_once_with(12345, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
