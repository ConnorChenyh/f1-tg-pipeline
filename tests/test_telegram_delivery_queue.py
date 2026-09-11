from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from analyzer.run_state import (
    STAGE_DELIVERED,
    RunState,
    load_run_state,
    mark_delivered,
    save_run_state,
)
from publisher.telegram_delivery_queue import deliver_pending_digests, enqueue_pending_delivery


class TelegramDeliveryQueueTests(unittest.TestCase):
    def test_failed_delivery_is_queued_once_and_removed_after_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output" / "2026-08-17_120000"
            (output_dir / "drafts" / "digest").mkdir(parents=True)
            config = {"telegram": {"pending_deliveries_path": "output/pending.json"}}

            enqueue_pending_delivery(root, config, output_dir)
            enqueue_pending_delivery(root, config, output_dir)
            queue_path = root / "output" / "pending.json"
            self.assertEqual(len(json.loads(queue_path.read_text(encoding="utf-8"))["deliveries"]), 1)

            send = Mock(return_value={"ok": True})
            self.assertEqual(deliver_pending_digests(root, config, send=send), 1)
            send.assert_called_once_with((output_dir / "drafts" / "digest").resolve(), config)
            self.assertEqual(json.loads(queue_path.read_text(encoding="utf-8"))["deliveries"], [])

    def test_failed_compensation_stays_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output" / "2026-08-17_120000"
            (output_dir / "drafts" / "digest").mkdir(parents=True)
            config = {"telegram": {"pending_deliveries_path": "output/pending.json"}}
            enqueue_pending_delivery(root, config, output_dir)

            self.assertEqual(deliver_pending_digests(root, config, send=Mock(side_effect=RuntimeError)), 0)
            queue_path = root / "output" / "pending.json"
            self.assertEqual(len(json.loads(queue_path.read_text(encoding="utf-8"))["deliveries"]), 1)

    def test_queue_save_failure_does_not_resend_a_committed_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output" / "2026-08-17_120000"
            (output_dir / "drafts" / "digest").mkdir(parents=True)
            save_run_state(output_dir, RunState(output_dir.name, "2026-08-17T12:00:00+00:00", 24))
            config = {"telegram": {"pending_deliveries_path": "output/pending.json"}}
            enqueue_pending_delivery(root, config, output_dir)
            send = Mock(return_value={"ok": True})

            def commit(path: Path) -> None:
                if mark_delivered(path) is None:
                    raise OSError("missing state")

            def committed(path: Path) -> bool:
                state = load_run_state(path)
                return state is not None and state.has(STAGE_DELIVERED)

            with patch(
                "publisher.telegram_delivery_queue._save_queue",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(OSError):
                    deliver_pending_digests(
                        root,
                        config,
                        send=send,
                        on_delivered=commit,
                        already_delivered=committed,
                    )

            self.assertTrue(load_run_state(output_dir).has(STAGE_DELIVERED))
            self.assertEqual(
                deliver_pending_digests(
                    root,
                    config,
                    send=send,
                    on_delivered=commit,
                    already_delivered=committed,
                ),
                0,
            )
            send.assert_called_once()
            queue_path = root / "output" / "pending.json"
            self.assertEqual(json.loads(queue_path.read_text(encoding="utf-8"))["deliveries"], [])

    def test_delivery_commit_failure_keeps_the_queue_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output" / "2026-08-17_120000"
            (output_dir / "drafts" / "digest").mkdir(parents=True)
            config = {"telegram": {"pending_deliveries_path": "output/pending.json"}}
            enqueue_pending_delivery(root, config, output_dir)

            delivered = deliver_pending_digests(
                root,
                config,
                send=Mock(return_value={"ok": True}),
                on_delivered=Mock(side_effect=OSError("state write failed")),
            )

            self.assertEqual(delivered, 0)
            queue_path = root / "output" / "pending.json"
            self.assertEqual(len(json.loads(queue_path.read_text(encoding="utf-8"))["deliveries"]), 1)

    def test_mark_delivered_reports_a_run_state_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output" / "2026-08-17_120000"
            save_run_state(output_dir, RunState(output_dir.name, "2026-08-17T12:00:00+00:00", 24))

            with patch("pathlib.Path.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    mark_delivered(output_dir)

            self.assertFalse(load_run_state(output_dir).has(STAGE_DELIVERED))


if __name__ == "__main__":
    unittest.main()
