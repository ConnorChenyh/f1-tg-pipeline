"""Offline regressions for evidence, selection and delivery boundary failures."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import yaml
from PIL import Image

from analyzer.evidence import find_evidence_posts, enrich_topics_with_evidence
from analyzer.normalize import normalize_posts
from analyzer.shortlist import shortlist_posts, _cross_source_count
from analyzer.topic_signature import topic_signature
from analyzer.topic_history import append_topic_history, filter_recent_topics
from analyzer.story_db import init_story_db, record_published_topics, filter_topics_seen_in_story_db
from analyzer.file_state import pipeline_lock
from analyzer.article_fetcher import enrich_evidence_with_articles
from collectors.base import PostItem
from collectors.rss import collect_rss
from publisher.telegram import push_digest_to_telegram, _post_telegram_json
from run import backfill_recent_topics

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 23, 4, tzinfo=timezone.utc)


def post(url, source="rss", title="Ferrari confirms revised floor for next race"):
    return PostItem(source=source, title=title, text=title, url=url, created_at=NOW)


class EvidenceRegressionTests(unittest.TestCase):
    def test_query_parameters_and_path_prefixes_identify_distinct_articles(self):
        posts = [post("https://example.com/article?id=1"), post("https://example.com/article?id=2")]
        matched = find_evidence_posts({"evidence_urls": [posts[0].url]}, posts)
        self.assertEqual(matched[0]["url"], posts[0].url)
        unknown = find_evidence_posts({"evidence_urls": ["https://example.com/art"]}, posts)
        self.assertEqual(unknown[0]["source"], "unknown")

    def test_tracking_parameters_do_not_prevent_exact_matching(self):
        matched = find_evidence_posts({"evidence_urls": ["https://example.com/article?b=2&a=1&utm_source=x"]},
                                      [post("https://example.com/article?a=1&b=2")])
        self.assertEqual(matched[0]["source"], "rss")

    def test_clustering_keeps_media_and_social_evidence_after_shortlist_limit(self):
        posts = [post("https://motorsport.com/f1/news/a"), post("https://reddit.com/r/f1/b", "reddit")]
        with patch("analyzer.normalize.datetime") as clock:
            clock.now.return_value = NOW
            normalized = normalize_posts(posts, 24)
        selected = shortlist_posts(normalized, {"shortlist": {"limit": 1}}, NOW)
        self.assertEqual(len(selected), 1)
        topics = enrich_topics_with_evidence([{"evidence_urls": [selected[0].url]}], selected)
        self.assertEqual({p["source"] for p in topics[0]["evidence_posts"]}, {"rss", "reddit"})
        self.assertEqual(_cross_source_count(posts[0], posts), 1)

    def test_distinct_media_domains_count_but_syndicated_same_domain_does_not(self):
        posts = [post("https://bbc.com/a"), post("https://autosport.com/a"), post("https://autosport.com/b")]
        self.assertEqual(_cross_source_count(posts[0], posts), 1)

    def test_editorial_cooldown_boundaries(self):
        config = yaml.safe_load((ROOT / "config.yaml").read_text())
        cases = json.loads((ROOT / "tests/fixtures/cooldown_cases.json").read_text())
        for case in cases:
            with self.subTest(title=case["title"]):
                self.assertEqual(topic_signature({"title_zh": case["title"]}, config), case["signature"])

    def test_sqlite_duplicates_can_backfill_but_cooled_topics_cannot(self):
        config = yaml.safe_load((ROOT / "config.yaml").read_text())
        topic = {"id": "one", "title_zh": "法拉利底板升级", "evidence_urls": ["https://example.com/a"],
                 "evidence_posts": [{"fetch_status": "ok", "article_content": "Verified article"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_story_db(root, config)
            record_published_topics([topic], root, config, NOW - timedelta(hours=2))
            kept, skipped = filter_topics_seen_in_story_db([topic], root, config, NOW)
            self.assertEqual(len(backfill_recent_topics(kept, skipped, [topic], 1)[0]), 1)
            cooled = dict(topic, title_zh="维斯塔潘转会传闻")
            append_topic_history([cooled], root, config, NOW - timedelta(hours=1))
            append_topic_history([topic], root, config, NOW)  # newer shared URL must not win
            kept, skipped = filter_recent_topics([cooled], root, config, NOW)
            self.assertEqual(backfill_recent_topics(kept, skipped, [cooled], 1)[0], [])

    def test_unpublished_history_expires_without_claiming_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topic = {"id": "x", "title_zh": "法拉利升级", "evidence_urls": ["https://example.com/a"]}
            append_topic_history([topic], root, {}, NOW, outcome="rejected")
            self.assertEqual(filter_recent_topics([topic], root, {}, NOW)[0], [])
            self.assertEqual(filter_recent_topics([topic], root, {}, NOW + timedelta(hours=2))[0], [topic])
            history = json.loads((root / "output/topic_history.json").read_text())
            self.assertIsNone(history[0]["published_at"])


class DeliveryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        (self.directory / "images").mkdir()
        self.environment = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "123"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def digest(self, count=1):
        (self.directory / "draft.json").write_text(json.dumps({"title": "Test", "telegram_title": "Frozen title",
            "items": [{"headline": "Headline", "content": "Body"} for _ in range(count)]}))
        for name in ["cover.png"] + [f"slide_{i:02d}.png" for i in range(1, count + 1)]:
            Image.new("RGB", (20, 20)).save(self.directory / "images" / name)

    def test_missing_or_truncated_images_block_before_any_send(self):
        self.digest()
        (self.directory / "images/slide_01.png").unlink()
        with patch("publisher.telegram._post_telegram_json") as send:
            with self.assertRaises(FileNotFoundError):
                push_digest_to_telegram(self.directory, {})
            send.assert_not_called()
        self.digest()
        (self.directory / "render_measurements.json").write_text('{"truncated_count": 1}')
        with patch("publisher.telegram._post_telegram_json") as send:
            with self.assertRaises(ValueError):
                push_digest_to_telegram(self.directory, {})
            send.assert_not_called()

    def test_partial_album_failure_retries_only_unsent_batch(self):
        self.digest(11)
        with patch("publisher.telegram._post_telegram_json", return_value={"ok": True}) as text, \
             patch("publisher.telegram._send_media_group", side_effect=[{"ok": True}, RuntimeError("offline"), {"ok": True}]) as media:
            with self.assertRaises(RuntimeError):
                push_digest_to_telegram(self.directory, {})
            result = push_digest_to_telegram(self.directory, {})
            self.assertEqual(result["image_count"], 12)
            self.assertEqual(text.call_count, 1)
            self.assertEqual([len(call.args[2]) for call in media.call_args_list], [10, 2, 2])
            push_digest_to_telegram(self.directory, {})
            self.assertEqual(media.call_count, 3)

    def test_changed_payload_does_not_mix_old_and_new_batches(self):
        self.digest()
        with patch("publisher.telegram._post_telegram_json", return_value={"ok": True}), \
             patch("publisher.telegram._send_media_group", return_value={"ok": True}):
            push_digest_to_telegram(self.directory, {})
        Image.new("RGB", (20, 20), "red").save(self.directory / "images/slide_01.png")
        with patch("publisher.telegram._post_telegram_json") as send:
            with self.assertRaisesRegex(ValueError, "payload changed"):
                push_digest_to_telegram(self.directory, {})
            send.assert_not_called()

    def test_corrupt_delivery_checkpoint_cannot_skip_a_batch(self):
        self.digest()
        with patch("publisher.telegram._post_telegram_json", return_value={"ok": True}), \
             patch("publisher.telegram._send_media_group", return_value={"ok": True}):
            push_digest_to_telegram(self.directory, {})
        path = self.directory / "telegram_delivery.json"
        checkpoint = json.loads(path.read_text())
        checkpoint["media"] = [None]
        path.write_text(json.dumps(checkpoint))
        with patch("publisher.telegram._post_telegram_json") as send:
            with self.assertRaisesRegex(ValueError, "checkpoint"):
                push_digest_to_telegram(self.directory, {})
            send.assert_not_called()

    def test_rate_limit_uses_server_delay_but_bad_request_does_not_retry(self):
        rate = Mock(status_code=429)
        rate.json.return_value = {"ok": False, "parameters": {"retry_after": 7}}
        ok = Mock(status_code=200)
        ok.json.return_value = {"ok": True}
        with patch("publisher.telegram.requests.post", side_effect=[rate, ok]) as request, \
             patch("publisher.telegram.time.sleep") as sleep:
            _post_telegram_json("fake", "sendMessage", {}, 1, 3, 1)
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(7)
        bad = Mock(status_code=400)
        bad.json.return_value = {"ok": False}
        with patch("publisher.telegram.requests.post", return_value=bad) as request:
            with self.assertRaises(RuntimeError):
                _post_telegram_json("fake", "sendMessage", {}, 1, 3, 0)
            self.assertEqual(request.call_count, 1)


class CollectionRegressionTests(unittest.TestCase):
    def test_article_cache_reuses_success_but_expires_and_never_persists_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"_runtime": {"root": tmp, "persist": True},
                      "article_fetch": {"delay_sec": 0, "use_jina": False, "cache_max_age_sec": 60}}
            topics = [{"evidence_posts": [{"url": "https://example.com/a"}]}]
            with patch("analyzer.article_fetcher.fetch_article_content", return_value={"fetch_status": "ok", "article_content": "Article"}) as fetch:
                first = enrich_evidence_with_articles(topics, config)
                second = enrich_evidence_with_articles(topics, config)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(first, second)
                self.assertTrue(first[0]["evidence_posts"][0]["content_hash"])
                cache_path = Path(tmp) / "output/article_cache.json"
                stored = json.loads(cache_path.read_text())
                for entry in stored.values():
                    entry["fetched_at"] = 0
                cache_path.write_text(json.dumps(stored))
                before = cache_path.read_bytes()
                config["_runtime"]["persist"] = False
                enrich_evidence_with_articles(topics, config)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(cache_path.read_bytes(), before)

    def test_article_fetch_concurrency_is_bounded_and_serial_per_host(self):
        import threading
        import time
        from urllib.parse import urlparse

        active, maximum = {}, {}
        total = [0, 0]
        guard = threading.Lock()
        both_started = threading.Event()

        def fetch(url, config):
            host = urlparse(url).hostname
            with guard:
                active[host] = active.get(host, 0) + 1
                maximum[host] = max(maximum.get(host, 0), active[host])
                total[0] += 1
                total[1] = max(total)
                if total[0] == 2:
                    both_started.set()
            if not both_started.wait(2):
                raise AssertionError("different hosts did not fetch concurrently")
            time.sleep(0.01)
            with guard:
                active[host] -= 1
                total[0] -= 1
            return {"fetch_status": "ok", "article_content": "Article"}

        topics = [{"evidence_posts": [{"url": url} for url in (
            "https://one.example/a", "https://two.example/a",
            "https://one.example/b", "https://two.example/b")]}]
        with patch("analyzer.article_fetcher.fetch_article_content", side_effect=fetch):
            result = enrich_evidence_with_articles(topics, {"article_fetch": {
                "use_jina": False, "concurrency": 2, "delay_sec": 0}})
        self.assertEqual(total[1], 2)
        self.assertEqual(maximum, {"one.example": 1, "two.example": 1})
        self.assertEqual(len(result[0]["evidence_posts"]), 4)

    def test_rss_conditional_request_reuses_body_and_records_source_health(self):
        body = b'<rss version="2.0"><channel><title>F1</title><item><title>News</title><link>https://example.com/a</link><pubDate>Wed, 23 Sep 2026 04:00:00 GMT</pubDate></item></channel></rss>'
        first = Mock(status_code=200, content=body, headers={"ETag": "revision1"})
        second = Mock(status_code=304, headers={})
        with tempfile.TemporaryDirectory() as tmp:
            config = {"rss_feeds": [{"name": "F1", "url": "https://example.com/rss"}],
                      "_runtime": {"root": tmp, "persist": True}}
            with patch("collectors.rss.requests.get", side_effect=[first, second]) as request, \
                 patch("collectors.rss.datetime") as clock:
                clock.now.return_value = NOW
                clock.side_effect = datetime
                self.assertEqual(len(collect_rss(config, 24)), 1)
                self.assertEqual(len(collect_rss(config, 24)), 1)
                self.assertEqual(request.call_args.kwargs["headers"]["If-None-Match"], "revision1")
                self.assertEqual(request.call_args.kwargs["timeout"], 15)
                self.assertTrue(config["_collection_status"][-1]["cached"])

    def test_rss_failure_is_distinct_from_empty_feed(self):
        config = {"rss_feeds": [{"name": "F1", "url": "https://example.com/rss"}],
                  "rss_fetch": {"retry": {"attempts": 1}}}
        with patch("collectors.rss.requests.get", side_effect=requests.Timeout):
            self.assertEqual(collect_rss(config, 24), [])
        self.assertEqual(config["_collection_status"][0]["status"], "failed")

    def test_second_process_cannot_take_pipeline_lock(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp, pipeline_lock(Path(tmp)):
            code = "from pathlib import Path; from analyzer.file_state import pipeline_lock;\ntry:\n with pipeline_lock(Path(__import__('sys').argv[1])): pass\nexcept BlockingIOError: raise SystemExit(23)"
            result = subprocess.run([sys.executable, "-c", code, tmp], cwd=ROOT, timeout=5)
            self.assertEqual(result.returncode, 23)
        with tempfile.TemporaryDirectory() as tmp:
            with pipeline_lock(Path(tmp)):
                pass
            with pipeline_lock(Path(tmp)):
                pass


if __name__ == "__main__":
    unittest.main()
