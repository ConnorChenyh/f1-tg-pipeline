from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import analyzer.corroboration as corr
from analyzer.evidence_gate import filter_topics_by_evidence_quality

NOW = datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)
CONFIG = {
    "corroboration": {"enabled": True, "lookback_days": 3, "max_topics": 4, "max_articles_per_topic": 2,
                      "min_authority": 3, "min_rerank_score": 0.8},
    "evidence_gate": {"min_article_chars": 800},
    "source_tiers": {
        "tier1_media": {"weight": 1.45, "domains": ["autosport.com"]},
        "specialist_media": {"weight": 1.25, "domains": ["racer.com"]},
        "social": {"weight": 0.72, "sources": ["reddit"]},
    },
}
BODY = "Franco Colapinto said abuse on social media hurts his future. " * 30


def _result(url: str, score: float = 2.0, authority: int = 3, date: str = "2026-10-15T16:00:00+00:00",
            title: str = "Colapinto urges fans to stop the abuse") -> dict:
    return {"url": url, "rerank_score": score, "authority": authority, "date": date, "title": title,
            "snippet": "snippet", "site_name": "site"}


def _reddit_topic(title: str = "Colapinto urges fans to stop the abuse [link]") -> dict:
    return {"id": "t1", "title_zh": "Colapinto 呼吁停止网暴", "heat_score": 70,
            "evidence_urls": ["https://www.reddit.com/r/formula1/comments/x"],
            "evidence_posts": [{"source": "reddit", "title": title, "url": "https://www.reddit.com/r/formula1/comments/x",
                                "fetch_status": "skipped", "article_content": ""}]}


def _tiered_topic() -> dict:
    return {"id": "t2", "title_zh": "已有正文", "heat_score": 80, "evidence_urls": ["https://www.autosport.com/a"],
            "evidence_posts": [{"source": "rss", "title": "Story", "url": "https://www.autosport.com/a",
                                "fetch_status": "ok", "article_content": BODY}]}


class SelectResultsTests(unittest.TestCase):
    def test_filters_irrelevant_old_future_social_and_low_authority_results(self) -> None:
        results = [
            _result("https://www.rermag.com/equipment", score=0.26),
            _result("https://www.autosport.com/old", date="2026-10-01T16:00:00+00:00"),
            _result("https://www.autosport.com/after-run", date="2026-10-19T16:00:00+00:00"),
            _result("https://www.reddit.com/r/formula1/comments/y"),
            _result("https://smallblog.example/post", authority=2),
            _result("https://www.autosport.com/known"),
            _result("https://racer.com/good", score=1.0),
        ]
        selected = corr.select_results(results, CONFIG, NOW, {"https://www.autosport.com/known"})
        self.assertEqual([item["url"] for item in selected], ["https://racer.com/good"])

    def test_tiered_then_score_one_per_host(self) -> None:
        results = [
            _result("https://news.example/high", score=2.7, authority=4),
            _result("https://news.example/other", score=2.5, authority=4),
            _result("https://blog.example/en", score=2.4, authority=3),
            _result("https://racer.com/one", score=1.2),
            _result("https://racer.com/two", score=1.5),
        ]
        selected = corr.select_results(results, CONFIG, NOW, set())
        self.assertEqual([item["url"] for item in selected],
                         ["https://racer.com/two", "https://news.example/high", "https://blog.example/en"])


class QueryTests(unittest.TestCase):
    def test_query_uses_english_title_and_date_limit(self) -> None:
        query = corr.build_query(_reddit_topic(), NOW, 3)
        self.assertEqual(query, "Colapinto urges fans to stop the abuse link lang:en after:2026-10-14")

    def test_long_title_is_cut_to_api_limit(self) -> None:
        query = corr.build_query(_reddit_topic("word " * 60), NOW, 3)
        self.assertLessEqual(len(query), corr.QUERY_MAX_CHARS)
        self.assertTrue(query.endswith(" lang:en after:2026-10-14"))


class CorroborateTopicsTests(unittest.TestCase):
    def _run(self, topics, results, fetched=None, error=None):
        def fake_post(url, headers, json, timeout):
            if error:
                raise error
            return SimpleNamespace(status_code=200, json=lambda: {"results": results},
                                   raise_for_status=lambda: None)

        fetched = fetched or {"fetch_status": "ok", "article_content": BODY, "fetch_method": "jina"}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             patch.object(corr.requests, "post", side_effect=fake_post) as post, \
             patch.object(corr, "fetch_article_content", return_value=dict(fetched)) as fetch:
            out = corr.corroborate_topics(topics, CONFIG, NOW, exclude_urls=set())
        return out, post, fetch

    def test_thin_topic_gains_article_evidence_and_passes_gate(self) -> None:
        out, post, _ = self._run([_reddit_topic(), _tiered_topic(), _tiered_topic()],
                                 [_result("https://racer.com/good"), _result("https://www.autosport.com/b")])
        self.assertEqual(post.call_count, 1)  # tiered topics are not searched
        topic = out[0]
        self.assertEqual(topic["corroboration"]["status"], "added")
        self.assertEqual(len(topic["corroboration"]["added"]), 2)
        self.assertIn("https://racer.com/good", topic["evidence_urls"])
        self.assertEqual(topic["evidence_posts"][-1]["source"], "search")
        kept, skipped = filter_topics_by_evidence_quality(out, CONFIG)
        self.assertEqual(len(kept), 3)
        self.assertEqual(skipped, [])

    def test_no_result_leaves_topic_for_evidence_gate(self) -> None:
        out, _, _ = self._run([_reddit_topic(), _tiered_topic(), _tiered_topic()], [])
        self.assertEqual(out[0]["corroboration"]["status"], "no_article_found")
        kept, skipped = filter_topics_by_evidence_quality(out, CONFIG)
        self.assertEqual([item["reason"] for item in skipped], ["social_only_without_article_content"])

    def test_short_fetched_body_is_not_added(self) -> None:
        out, _, _ = self._run([_reddit_topic()], [_result("https://racer.com/good")],
                              fetched={"fetch_status": "ok", "article_content": "short", "fetch_method": "html"})
        self.assertEqual(out[0]["corroboration"]["added"], [])

    def test_search_failure_keeps_topic_unchanged(self) -> None:
        import requests
        out, _, fetch = self._run([_reddit_topic()], [], error=requests.ConnectionError("down"))
        self.assertTrue(out[0]["corroboration"]["status"].startswith("search_failed"))
        self.assertEqual(len(out[0]["evidence_posts"]), 1)
        fetch.assert_not_called()

    def test_topic_without_query_is_recorded_and_status_reaches_gate_skip(self) -> None:
        out, post, _ = self._run([_reddit_topic("Max Verstappen’s GT3 weekend in Portimão"),
                                  _tiered_topic(), _tiered_topic()], [])
        post.assert_not_called()
        self.assertEqual(out[0]["corroboration"]["status"], "no_query")
        kept, skipped = filter_topics_by_evidence_quality(out, CONFIG)
        self.assertEqual(len(kept), 2)
        self.assertEqual(skipped[0]["corroboration"]["status"], "no_query")

    def test_gate_skip_without_corroboration_record_has_no_key(self) -> None:
        topics = [_reddit_topic(), _tiered_topic(), _tiered_topic()]
        _, skipped = filter_topics_by_evidence_quality(topics, CONFIG)
        self.assertNotIn("corroboration", skipped[0])

    def test_disabled_by_default(self) -> None:
        topics = [_reddit_topic()]
        self.assertIs(corr.corroborate_topics(topics, {}, NOW), topics)


if __name__ == "__main__":
    unittest.main()
