from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import scheduler
from generator.digest_writer import generate_digest, repair_digest


class GuardReworkTests(unittest.TestCase):
    def test_same_article_id_restores_exact_evidence_url_without_model(self):
        original = "https://www.motorsport.com/f1/news/mclaren-fuel-system/10863082/?utm_source=RSS"
        invented = "https://www.motorsport.com/f1/news/mclaren-costly-fuel-system/10863082"
        client = SimpleNamespace(chat_json=MagicMock())
        draft, _ = repair_digest(client, {"sources": [invented]}, [{"evidence_urls": [original]}],
            None, "日报", [{"code": "unknown_source", "severity": "error"}])
        self.assertEqual(draft["sources"], ["https://www.motorsport.com/f1/news/mclaren-fuel-system/10863082"])
        client.chat_json.assert_not_called()

    def test_different_article_or_domain_does_not_get_silently_replaced(self):
        original = "https://www.motorsport.com/f1/news/original/10863082"
        for unknown in ["https://www.motorsport.com/f1/news/other/10863083",
                        "https://example.com/f1/news/other/10863082"]:
            draft = {"sources": [unknown], "items": [{"headline": "标题", "content": "正文"}]}
            with patch("generator.digest_writer.final_review_digest", return_value=(draft, [])) as review:
                repaired, _ = repair_digest(None, draft, [{"evidence_urls": [original]}], None, "日报",
                    [{"code": "unknown_source", "severity": "error"}])
            self.assertEqual(repaired["sources"], [unknown])
            review.assert_called_once()

    def test_final_review_new_bad_source_is_returned_for_rework(self):
        good = {"title": "日报", "items": [{"ordinal": "一", "headline": "升级", "content": "车队调整底板。"}],
                "sources": ["https://example.com/original"]}
        bad = copy.deepcopy(good)
        bad["sources"] = ["https://example.com/invented"]
        client = SimpleNamespace(model_writer="writer", chat_json=MagicMock(return_value=good))
        ctx = SimpleNamespace(to_prompt_block=lambda: "ctx", generated_at=datetime.now(timezone.utc))
        with patch("generator.digest_writer.final_review_digest", side_effect=[(bad, []), (good, [])]) as review:
            draft, notes, codes = generate_digest(client, [{"evidence_urls": good["sources"]}], ctx,
                "日报", min_items=1, fact_check_enabled=False)
        self.assertEqual(codes, [])
        self.assertEqual(draft["sources"], good["sources"])
        self.assertEqual(review.call_count, 2)
        self.assertEqual(review.call_args.kwargs["quality_issues"][0]["code"], "unknown_source")

    def test_exhausted_rework_keeps_blocker(self):
        bad = {"items": [{"ordinal": "一", "headline": "升级", "content": "正文"}],
               "sources": ["https://example.com/invented"]}
        client = SimpleNamespace(model_writer="writer", chat_json=MagicMock(return_value=bad))
        ctx = SimpleNamespace(to_prompt_block=lambda: "ctx", generated_at=datetime.now(timezone.utc))
        with patch("generator.digest_writer.final_review_digest", return_value=(bad, [])) as review:
            _, _, codes = generate_digest(client, [{"evidence_urls": ["https://example.com/original"]}], ctx,
                "日报", min_items=1, fact_check_enabled=False)
        self.assertEqual(codes, ["unknown_source"])
        self.assertEqual(review.call_count, 3)


class ScheduledRecoveryTests(unittest.TestCase):
    def test_failed_job_resumes_before_returning_success(self):
        with patch.object(scheduler, "_run_pipeline_once", side_effect=[1, 0]) as execute:
            self.assertEqual(scheduler.run_pipeline(Path("config.yaml"), 24, True), 0)
        self.assertEqual(execute.call_count, 2)
        self.assertFalse(execute.call_args_list[0].kwargs["resume"])
        self.assertTrue(execute.call_args_list[1].kwargs["resume"])

    def test_exhausted_job_alerts_and_returns_failure(self):
        with patch.object(scheduler, "_run_pipeline_once", return_value=1) as execute, \
             patch.object(scheduler, "load_config", return_value={}), \
             patch("publisher.telegram_text.send_text_to_telegram") as alert:
            self.assertEqual(scheduler.run_pipeline(Path("config.yaml"), 24, True), 1)
        self.assertEqual(execute.call_count, 3)
        alert.assert_called_once()

    def test_success_has_no_retry(self):
        with patch.object(scheduler, "_run_pipeline_once", return_value=0) as execute:
            self.assertEqual(scheduler.run_pipeline(Path("config.yaml"), 24, True), 0)
        execute.assert_called_once()
