from __future__ import annotations

import unittest
from datetime import datetime, timezone

from analyzer.fallback_topics import build_fallback_article_topics
from collectors.base import PostItem


class FallbackTopicTests(unittest.TestCase):
    def test_builds_article_backed_fallbacks_and_skips_social_urls(self) -> None:
        now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
        posts = [
            PostItem(
                source="reddit",
                title="Reddit image",
                text="Reddit image",
                url="https://i.redd.it/example.jpg",
                created_at=now,
                extra={"shortlist_score": 100},
            ),
            PostItem(
                source="rss",
                title="Williams delay analysis",
                text="James Vowles explains Williams delays.",
                url="https://www.motorsport.com/f1/news/williams-delay/10839043/",
                created_at=now,
                extra={"shortlist_score": 90},
            ),
            PostItem(
                source="rss",
                title="Perez mental toll",
                text="Perez talks about Red Bull stint.",
                url="https://www.motorsport.com/f1/news/perez-red-bull/10839041/",
                created_at=now,
                extra={"shortlist_score": 80},
            ),
        ]

        topics = build_fallback_article_topics(posts, existing_topics=[], needed=2)

        self.assertEqual([topic["id"] for topic in topics], ["fallback_01", "fallback_02"])
        self.assertEqual(topics[0]["evidence_urls"], ["https://www.motorsport.com/f1/news/williams-delay/10839043/"])
        self.assertEqual(topics[1]["evidence_urls"], ["https://www.motorsport.com/f1/news/perez-red-bull/10839041/"])

    def test_skips_existing_topic_urls(self) -> None:
        now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
        posts = [
            PostItem(
                source="rss",
                title="Already used",
                text="Already used",
                url="https://www.autosport.com/f1/news/used/1/?utm_source=rss",
                created_at=now,
            ),
            PostItem(
                source="rss",
                title="Fresh article",
                text="Fresh article",
                url="https://www.autosport.com/f1/news/fresh/2/",
                created_at=now,
            ),
        ]
        existing = [{"evidence_urls": ["https://www.autosport.com/f1/news/used/1"]}]

        topics = build_fallback_article_topics(posts, existing_topics=existing, needed=2)

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["title_zh"], "Fresh article")


if __name__ == "__main__":
    unittest.main()
