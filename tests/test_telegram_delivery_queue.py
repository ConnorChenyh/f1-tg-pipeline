from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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


if __name__ == "__main__":
    unittest.main()
