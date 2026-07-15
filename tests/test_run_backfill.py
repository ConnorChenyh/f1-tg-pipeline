from __future__ import annotations

import unittest

from run import backfill_recent_topics


class RunBackfillTests(unittest.TestCase):
    def test_backfills_recent_duplicates_to_meet_minimum(self) -> None:
        fresh = [{"id": "topic_04", "title_zh": "新话题"}]
        original = [
            {"id": "topic_01", "title_zh": "旧话题一"},
            {"id": "topic_02", "title_zh": "旧话题二"},
            {"id": "topic_03", "title_zh": "低证据话题"},
            {"id": "topic_04", "title_zh": "新话题"},
        ]
        skipped = [
            {
                "id": "topic_01",
                "title_zh": "旧话题一",
                "reason": "shared_url:https://example.com/1",
                "duplicate_age_hours": 1.0,
            },
            {
                "id": "topic_02",
                "title_zh": "旧话题二",
                "reason": "text_similarity:0.91",
                "duplicate_age_hours": 2.0,
            },
            {"id": "topic_03", "title_zh": "低证据话题", "reason": "social_only_without_article_content"},
        ]

        topics, remaining = backfill_recent_topics(fresh, skipped, original, 3)

        self.assertEqual([topic["id"] for topic in topics], ["topic_04", "topic_01", "topic_02"])
        self.assertTrue(any(item["id"] == "topic_03" for item in remaining))
        self.assertTrue(
            any(
                item["id"] == "topic_01" and item["reason"] == "backfilled_recent_duplicate_for_min_items"
                for item in remaining
            )
        )

    def test_does_not_backfill_low_evidence_only_topics(self) -> None:
        fresh = [{"id": "topic_01"}]
        original = [{"id": "topic_02"}]
        skipped = [{"id": "topic_02", "reason": "social_only_without_article_content"}]

        topics, remaining = backfill_recent_topics(fresh, skipped, original, 2)

        self.assertEqual(topics, fresh)
        self.assertEqual(remaining, skipped)

    def test_does_not_backfill_old_recent_duplicates(self) -> None:
        fresh = [{"id": "topic_01"}]
        original = [{"id": "topic_02"}]
        skipped = [
            {
                "id": "topic_02",
                "reason": "shared_url:https://example.com/old",
                "duplicate_age_hours": 20.0,
            }
        ]

        topics, remaining = backfill_recent_topics(fresh, skipped, original, 2, max_age_hours=6)

        self.assertEqual(topics, fresh)
        self.assertEqual(remaining, skipped)

    def test_does_not_backfill_topic_signature_duplicates(self) -> None:
        fresh = [{"id": "topic_01"}]
        original = [{"id": "topic_02"}]
        skipped = [
            {
                "id": "topic_02",
                "reason": "topic_signature:verstappen_future",
                "duplicate_age_hours": 1.0,
            }
        ]

        topics, remaining = backfill_recent_topics(fresh, skipped, original, 2)

        self.assertEqual(topics, fresh)
        self.assertEqual(remaining, skipped)


if __name__ == "__main__":
    unittest.main()
