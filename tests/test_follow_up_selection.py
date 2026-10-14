from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run as run_module
from analyzer.context import RunContext as RealRunContext
from analyzer.evidence_gate import filter_topics_by_evidence_quality
from analyzer.topic_history import (
    drop_published_posts,
    published_urls,
    recent_published_topics,
)
from analyzer.topics import extract_topics
from collectors.base import PostItem
from generator.deepseek_client import RunDeadlineExceeded
from generator.evidence_pack import build_topic_grounding
from tests.harness import RunHarness, _base_config, _stub_post, _stub_topic

NOW = datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc)
OLD_URL = "https://www.espn.com/f1/story/_/id/1/old-standings-maths"
NEW_URL = "https://www.motorsport.com/f1/news/antonelli-track-limits"


def _post(url: str, related: list[str] | None = None) -> PostItem:
    post = PostItem(source="rss", text="body", title=url.rsplit("/", 1)[-1], url=url, created_at=NOW)
    post.extra["related_posts"] = [
        PostItem(source="rss", text="body", title=item, url=item, created_at=NOW).to_dict()
        for item in related or []
    ]
    return post


def _history(root: Path, entries: list[dict]) -> dict:
    config = {"topic_history": {"enabled": True, "dedupe_days": 7, "path": str(root / "history.json")}}
    (root / "history.json").write_text(json.dumps(entries), encoding="utf-8")
    return config


def _entry(hours_ago: float, urls: list[str], outcome: str = "delivered", title: str = "旧话题") -> dict:
    at = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {
        "recorded_at": at,
        "published_at": at if outcome == "delivered" else None,
        "outcome": outcome,
        "title_zh": title,
        "summary": f"{title}摘要",
        "evidence_urls": urls,
    }


class PublishedHistoryTests(unittest.TestCase):
    def test_only_delivered_urls_inside_dedupe_window_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _history(Path(tmp), [
                _entry(24, [OLD_URL]),
                _entry(2, ["https://a.example/rejected"], outcome="rejected"),
                _entry(24 * 8, ["https://a.example/expired"]),
            ])
            self.assertEqual(published_urls(Path(tmp), config, NOW), {OLD_URL})

    def test_recent_topics_cover_follow_up_window_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _history(Path(tmp), [
                _entry(24, [OLD_URL], title="昨天"),
                _entry(48, [OLD_URL], title="前天"),
                _entry(24 * 4, [OLD_URL], title="四天前"),
            ])
            titles = [item["title_zh"] for item in recent_published_topics(Path(tmp), config, NOW)]
            self.assertEqual(titles, ["前天", "昨天"])
            config["topic_history"]["follow_up_context_days"] = 0
            self.assertEqual(recent_published_topics(Path(tmp), config, NOW), [])


class DropPublishedPostsTests(unittest.TestCase):
    def test_published_post_removed_and_unrelated_post_kept(self) -> None:
        kept, removed = drop_published_posts([_post(OLD_URL), _post(NEW_URL)], {OLD_URL})
        self.assertEqual([post.url for post in kept], [NEW_URL])
        self.assertEqual(removed, [OLD_URL])

    def test_fresh_related_post_takes_over_when_lead_was_published(self) -> None:
        other = "https://www.autosport.com/f1/news/antonelli-brakes"
        kept, removed = drop_published_posts([_post(OLD_URL, related=[NEW_URL, other])], {OLD_URL})
        self.assertEqual([post.url for post in kept], [NEW_URL])
        self.assertEqual([item["url"] for item in kept[0].extra["related_posts"]], [other])
        self.assertEqual(removed, [OLD_URL])

    def test_published_related_post_is_dropped_without_mutating_input(self) -> None:
        original = _post(NEW_URL, related=[OLD_URL])
        kept, removed = drop_published_posts([original], {OLD_URL})
        self.assertEqual(kept[0].extra["related_posts"], [])
        self.assertEqual(len(original.extra["related_posts"]), 1)
        self.assertEqual(removed, [OLD_URL])


class FollowUpPromptTests(unittest.TestCase):
    def test_recent_topics_reach_extraction_prompt(self) -> None:
        seen = {}

        def chat_json(model, system, user, **kwargs):
            seen["user"] = user
            return [{"id": "topic_01", "title_zh": "话题", "heat_score": 80,
                     "publish_recommendation": "publish", "evidence_urls": [NEW_URL]}]

        client = SimpleNamespace(model_topics="m", chat_json=chat_json)
        extract_topics(client, [_post(NEW_URL)], 55, SimpleNamespace(to_prompt_block=lambda: "ctx"),
                       min_topics=1, max_topics=5,
                       recent_topics=[{"title_zh": "McLaren 队内相撞", "summary": "摘要"}])
        self.assertIn("McLaren 队内相撞", seen["user"])
        self.assertIn("no new development", seen["user"])

    def test_follow_up_fields_reach_writer_grounding(self) -> None:
        topic = dict(_stub_topic(), follow_up_of="McLaren 队内相撞", new_development="Stella 表态")
        grounding = build_topic_grounding(topic)
        self.assertEqual(grounding["follow_up_of"], "McLaren 队内相撞")
        self.assertEqual(grounding["new_development"], "Stella 表态")


class ArticleLengthGateTests(unittest.TestCase):
    def _topic(self, chars: int, heat: int = 75) -> dict:
        return {"id": f"t{chars}", "title_zh": "t", "heat_score": heat, "evidence_posts": [
            {"source": "reddit", "fetch_status": "ok", "article_content": "x" * chars}]}

    def test_templated_page_does_not_count_as_article(self) -> None:
        config = {"evidence_gate": {"min_article_chars": 800}}
        backed = [self._topic(3000), self._topic(4000)]
        kept, skipped = filter_topics_by_evidence_quality(backed + [self._topic(677)], config)
        self.assertEqual([t["id"] for t in kept], ["t3000", "t4000"])
        self.assertEqual(skipped[0]["reason"], "social_only_without_article_content")

    def test_default_keeps_any_non_empty_body(self) -> None:
        kept, skipped = filter_topics_by_evidence_quality(
            [self._topic(3000), self._topic(4000), self._topic(677)], {})
        self.assertEqual(len(kept), 3)
        self.assertEqual(skipped, [])


class RunWiringTests(unittest.TestCase):
    def test_published_url_never_reaches_topic_extraction(self) -> None:
        harness = RunHarness()
        client = harness._fake_client(harness._scripts(), [])
        fresh = _stub_post(datetime.now(timezone.utc))
        fresh.url = NEW_URL
        old = _stub_post(datetime.now(timezone.utc))
        old.url = OLD_URL
        old.title = "Old standings maths"

        def stop_at_digest(*args, **kwargs):
            raise RunDeadlineExceeded("stop after topic selection")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            history = root / "output" / "topic_history.json"
            history.write_text(json.dumps([{
                "recorded_at": at, "published_at": at, "outcome": "delivered",
                "title_zh": "积分推演", "summary": "s", "evidence_urls": [OLD_URL],
            }]), encoding="utf-8")
            config["topic_history"]["path"] = str(history)
            topic = dict(_stub_topic(), evidence_urls=[NEW_URL])
            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[fresh, old]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_calendar", return_value=False), \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=client), \
                 patch.object(run_module, "extract_topics", return_value=[topic]) as extract, \
                 patch.object(run_module, "generate_digest", side_effect=stop_at_digest), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24"]):
                ctx_cls.now.return_value = RealRunContext.now(24)
                run_module.main()
            posts = extract.call_args.args[1]
            self.assertEqual([post.url for post in posts], [NEW_URL])
            self.assertEqual([t["title_zh"] for t in extract.call_args.kwargs["recent_topics"]], ["积分推演"])
            status = json.loads((root / "output" / "run1" / "topics_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["removed_published_urls"], [OLD_URL])


if __name__ == "__main__":
    unittest.main()
