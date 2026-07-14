from __future__ import annotations

import unittest

from generator.evidence_pack import build_topic_grounding, normalize_source_url
from generator.quality_guard import blocking_issues, validate_digest


class EvidencePackTests(unittest.TestCase):
    def test_grounding_prefers_article_content_and_normalizes_urls(self) -> None:
        topic = {
            "id": "topic_01",
            "title_zh": "测试话题",
            "summary": "模型摘要",
            "evidence_posts": [
                {
                    "source": "rss",
                    "title": "RSS title",
                    "url": "https://example.com/f1/story/?utm_source=RSS&utm_campaign=x&id=9",
                    "created_at": "2026-07-13T08:00:00+00:00",
                    "text": "RSS snippet only",
                    "article_content": "Full article content with enough detail for the model.",
                    "fetch_status": "ok",
                }
            ],
        }

        grounding = build_topic_grounding(topic)
        evidence = grounding["evidence"][0]

        self.assertEqual(evidence["content_basis"], "article_content")
        self.assertEqual(evidence["fetch_status"], "ok")
        self.assertIn("Full article content", evidence["content"])
        self.assertEqual(evidence["canonical_url"], "https://example.com/f1/story?id=9")

    def test_normalize_source_url_strips_tracking_fragments(self) -> None:
        self.assertEqual(
            normalize_source_url("HTTPS://Example.com/F1/story/?utm_medium=feed&ref=home#comments"),
            "https://example.com/F1/story",
        )


class QualityGuardTests(unittest.TestCase):
    def test_goodwood_semantic_error_is_blocking(self) -> None:
        topics = [
            {
                "evidence_urls": ["https://www.motorsport.com/f1/news/goodwood/10838177/"],
                "evidence_posts": [
                    {
                        "url": "https://www.motorsport.com/f1/news/goodwood/10838177/?utm_source=RSS",
                    }
                ],
            }
        ]
        draft = {
            "title": "围场过去24H新闻",
            "hook": "过去24小时，围场暗流涌动。",
            "items": [
                {
                    "ordinal": "一",
                    "headline": "古德伍德名场面",
                    "content": "新科冠军诺里斯驾驶MCL60登上古德伍德庄园阳台，复刻童年梦想。",
                }
            ],
            "sources": ["https://www.motorsport.com/f1/news/goodwood/10838177/"],
        }

        issues = validate_digest(draft, topics, min_items=1, max_items=5)
        codes = {issue.code for issue in issues}

        self.assertIn("event_conflation_balcony", codes)
        self.assertIn("ambiguous_new_champion", codes)
        self.assertIn("overdramatized_tone", codes)
        self.assertEqual(
            [issue.code for issue in blocking_issues(issues)],
            ["event_conflation_balcony"],
        )

    def test_sources_must_come_from_evidence(self) -> None:
        topics = [{"evidence_urls": ["https://example.com/source"]}]
        draft = {
            "title": "围场过去24H新闻",
            "hook": "",
            "items": [{"ordinal": "一", "headline": "标题", "content": "正文"}],
            "sources": ["https://example.com/other"],
        }

        codes = {issue.code for issue in validate_digest(draft, topics, min_items=1, max_items=5)}

        self.assertIn("unknown_source", codes)

    def test_possible_overtranslated_technical_term_is_warned_not_blocked(self) -> None:
        draft = {
            "title": "围场过去24H新闻",
            "hook": "",
            "items": [
                {
                    "ordinal": "一",
                    "headline": "法拉利技术调整",
                    "content": "法拉利可能移除STM吹气排气系统，改用低阻尾翼。",
                }
            ],
            "sources": ["https://example.com/source"],
        }

        issues = validate_digest(
            draft,
            [{"evidence_urls": ["https://example.com/source"]}],
            min_items=1,
            max_items=5,
        )
        codes = {issue.code for issue in issues}

        self.assertIn("possibly_overtranslated_technical_term", codes)
        self.assertNotIn(
            "possibly_overtranslated_technical_term",
            {issue.code for issue in blocking_issues(issues)},
        )

    def test_social_video_anchor_accepts_video_context(self) -> None:
        draft = {
            "title": "围场过去24H新闻",
            "hook": "",
            "items": [
                {
                    "ordinal": "一",
                    "headline": "花絮",
                    "content": "社交媒体视频还拍到汉密尔顿对试图拍照的诺里斯竖中指。",
                }
            ],
            "sources": ["https://example.com/source"],
        }

        issues = validate_digest(
            draft,
            [{"evidence_urls": ["https://example.com/source"]}],
            min_items=1,
            max_items=5,
        )
        codes = {issue.code for issue in issues}

        self.assertNotIn("unanchored_social_video_claim", codes)

    def test_rich_evidence_item_too_short_is_warned(self) -> None:
        draft = {
            "title": "围场过去24H新闻",
            "hook": "",
            "items": [
                {
                    "ordinal": "一",
                    "headline": "比利时站预热",
                    "content": "比利时站本周末开赛，现役车手中仅四人曾在斯帕夺冠。",
                }
            ],
            "sources": ["https://example.com/source"],
        }

        issues = validate_digest(
            draft,
            [
                {
                    "evidence_urls": ["https://example.com/source"],
                    "evidence_posts": [
                        {"url": "https://example.com/source", "fetch_status": "ok", "article_content": "x" * 500}
                    ],
                }
            ],
            min_items=1,
            max_items=5,
            min_item_chars=420,
        )
        codes = {issue.code for issue in issues}

        self.assertIn("too_short_rich_evidence_item", codes)
        self.assertNotIn(
            "too_short_rich_evidence_item",
            {issue.code for issue in blocking_issues(issues)},
        )


if __name__ == "__main__":
    unittest.main()
