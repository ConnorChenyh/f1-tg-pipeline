from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer.topic_history import append_topic_history, filter_recent_topics


class TopicHistoryTests(unittest.TestCase):
    def test_recent_topic_with_same_source_url_is_filtered(self) -> None:
        now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        config = {"topic_history": {"enabled": True, "dedupe_days": 7, "path": "topic_history.json"}}
        old_topic = {
            "id": "topic_01",
            "title_zh": "维斯塔潘未来去向再引猜测",
            "summary": "ESPN 梳理红牛合同与退出条款。",
            "evidence_urls": ["https://example.com/story?utm_source=rss"],
        }
        new_topic = {
            "id": "topic_02",
            "title_zh": "维斯塔潘去留讨论升温",
            "summary": "社交媒体继续讨论红牛合同。",
            "evidence_urls": ["https://example.com/story"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            append_topic_history([old_topic], root, config, now - timedelta(days=1))
            fresh, skipped = filter_recent_topics([new_topic], root, config, now)

        self.assertEqual(fresh, [])
        self.assertEqual(skipped[0]["id"], "topic_02")
        self.assertTrue(skipped[0]["reason"].startswith("shared_url:"))
        self.assertEqual(skipped[0]["duplicate_age_hours"], 24.0)
        self.assertIsNotNone(skipped[0]["duplicate_published_at"])

    def test_old_topic_outside_window_is_allowed(self) -> None:
        now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        config = {"topic_history": {"enabled": True, "dedupe_days": 7, "path": "topic_history.json"}}
        topic = {
            "id": "topic_01",
            "title_zh": "比利时站预热",
            "summary": "斯帕赛前消息。",
            "evidence_urls": ["https://example.com/spa"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            append_topic_history([topic], root, config, now - timedelta(days=8))
            fresh, skipped = filter_recent_topics([topic], root, config, now)

        self.assertEqual([item["id"] for item in fresh], ["topic_01"])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
