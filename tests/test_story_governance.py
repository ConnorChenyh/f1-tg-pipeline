from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer.evidence_gate import filter_topics_by_evidence_quality
from analyzer.shortlist import shortlist_posts
from analyzer.story_db import filter_topics_seen_in_story_db, init_story_db, record_candidates, record_published_topics
from collectors.base import PostItem


class StoryGovernanceTests(unittest.TestCase):
    def test_shortlist_prefers_article_backed_tiered_sources(self) -> None:
        now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        config = {
            "shortlist": {"enabled": True, "limit": 1, "min_score": 0},
            "source_tiers": {
                "tier1_media": {"weight": 2.0, "domains": ["autosport.com"]},
                "social": {"weight": 0.5, "sources": ["reddit"]},
            },
        }
        posts = [
            PostItem(
                source="reddit",
                title="Same F1 story discussed by fans",
                text="Same F1 story discussed by fans",
                url="https://www.reddit.com/r/formula1/comments/abc",
                created_at=now,
                likes=200,
            ),
            PostItem(
                source="rss",
                title="Same F1 story confirmed by Autosport",
                text="Same F1 story confirmed by Autosport",
                url="https://www.autosport.com/f1/news/story",
                created_at=now,
                likes=0,
            ),
        ]

        selected = shortlist_posts(posts, config, now)

        self.assertEqual(len(selected), 1)
        self.assertIn("autosport.com", selected[0].url)
        self.assertIn("external_article", selected[0].extra["shortlist_reasons"])

    def test_shortlist_caps_social_only_media_posts(self) -> None:
        now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        config = {
            "shortlist": {
                "enabled": True,
                "limit": 2,
                "min_score": 0,
                "social_only_score_cap": 850,
            }
        }
        posts = [
            PostItem(
                source="reddit",
                title="Popular image post",
                text="Popular image post",
                url="https://i.redd.it/example.jpeg",
                created_at=now,
                likes=10000,
                raw_score=10000,
            ),
            PostItem(
                source="rss",
                title="Confirmed report",
                text="Confirmed report",
                url="https://www.motorsport.com/f1/news/confirmed-report",
                created_at=now,
                raw_score=1000,
            ),
        ]

        selected = shortlist_posts(posts, config, now)

        self.assertIn("motorsport.com", selected[0].url)
        self.assertIn("social_only_cap:850", selected[1].extra["shortlist_reasons"])

    def test_shortlist_limits_social_only_count(self) -> None:
        now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        config = {
            "shortlist": {
                "enabled": True,
                "limit": 10,
                "max_social_only_posts": 1,
            }
        }
        posts = [
            PostItem(
                source="reddit",
                title=f"Image post {index}",
                text=f"Image post {index}",
                url=f"https://i.redd.it/example-{index}.jpeg",
                created_at=now,
                likes=100 + index,
                raw_score=100 + index,
            )
            for index in range(3)
        ]
        posts.append(
            PostItem(
                source="rss",
                title="Article post",
                text="Article post",
                url="https://www.autosport.com/f1/news/article-post",
                created_at=now,
                raw_score=10,
            )
        )

        selected = shortlist_posts(posts, config, now)

        social_only = [item for item in selected if "i.redd.it" in item.url]
        self.assertEqual(len(social_only), 1)
        self.assertTrue(any("autosport.com" in item.url for item in selected))

    def test_evidence_gate_skips_social_only_when_article_backed_topics_exist(self) -> None:
        config = {"evidence_gate": {"enabled": True, "allow_social_only_heat": 88, "min_article_backed_topics": 1}}
        topics = [
            {
                "id": "article",
                "heat_score": 60,
                "evidence_posts": [{"source": "rss", "fetch_status": "ok", "article_content": "full story"}],
            },
            {
                "id": "social",
                "title_zh": "社交传闻",
                "heat_score": 70,
                "evidence_posts": [{"source": "reddit", "fetch_status": "skipped", "article_content": ""}],
            },
        ]

        kept, skipped = filter_topics_by_evidence_quality(topics, config)

        self.assertEqual([topic["id"] for topic in kept], ["article"])
        self.assertEqual(skipped[0]["id"], "social")

    def test_story_db_records_and_filters_recent_published_topic(self) -> None:
        now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        config = {
            "story_db": {"enabled": True, "path": "story.sqlite3", "retention_days": 30},
            "topic_history": {"dedupe_days": 7},
        }
        topic = {
            "id": "topic_01",
            "title_zh": "维斯塔潘未来去向",
            "summary": "报道讨论合同条款。",
            "evidence_urls": ["https://www.espn.com/f1/story?id=1&utm_source=rss"],
        }
        candidate = PostItem(
            source="rss",
            title="Verstappen future",
            text="Contract report",
            url="https://www.espn.com/f1/story?id=1",
            created_at=now,
            raw_score=10,
            extra={"shortlist_score": 25},
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_story_db(root, config)
            record_candidates([candidate], root, config, "run", now)
            record_published_topics([topic], root, config, now - timedelta(days=1))
            fresh, skipped = filter_topics_seen_in_story_db([topic], root, config, now)

        self.assertEqual(fresh, [])
        self.assertEqual(skipped[0]["id"], "topic_01")
        self.assertTrue(skipped[0]["reason"].startswith("story_db:"))


if __name__ == "__main__":
    unittest.main()
