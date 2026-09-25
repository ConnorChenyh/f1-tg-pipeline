from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from generator.images import (
    RENDER_MEASUREMENTS_FILENAME,
    TEXT_WIDTH_RATIO,
    _load_font,
    _draw_multiline,
    render_digest_summary_card,
    _wrap_text,
    generate_images_for_digest,
    render_item_card_with_measure,
)


class DigestImageTests(unittest.TestCase):
    def test_wrap_text_keeps_english_words_intact(self) -> None:
        draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
        font = _load_font(30)
        word_width = draw.textbbox((0, 0), "McLaren", font=font)[2]

        lines = _wrap_text(draw, "前McLaren后 Mercedes-AMG", font, word_width - 1)

        self.assertEqual(lines, ["前", "McLaren", "后", "Mercedes-AMG"])

    def test_chinese_punctuation_wrap_preserves_text_and_width(self) -> None:
        draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
        font = _load_font(30)
        text = "升级：新底盘减重，车手称「仍待验证」；备选方案（尚未确认）。"
        for width in (130, 180, 230):
            lines = _wrap_text(draw, text, font, width)
            self.assertEqual("".join(lines), text)
            for line in lines:
                self.assertNotIn(line[0], "，。；：、）」")
                self.assertNotIn(line[-1], "（「")
                self.assertLessEqual(draw.textbbox((0, 0), line, font=font)[2], width)

    def test_spaced_quotes_and_paragraphs(self) -> None:
        draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
        font = _load_font(30)
        text = "  车手称「 免费圈速 」 。\n\n效果仍待验证。"
        lines = _wrap_text(draw, text, font, 160)
        self.assertIn("", lines)
        self.assertEqual(re.sub(r"\s+", "", "".join(lines)), re.sub(r"\s+", "", text))
        self.assertTrue(all(not line or line[0] not in "」。" for line in lines))

    def test_heading_does_not_leave_single_character_tail(self) -> None:
        draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
        font = _load_font(30)
        width = draw.textbbox((0, 0), "首次测", font=font)[2]
        with patch.object(draw, "text", wraps=draw.text) as render:
            _draw_multiline(draw, "首次测试", (0, 0), font, "black", width)
        lines = [call.args[1] for call in render.call_args_list]
        self.assertEqual(lines, ["首次", "测试"])

    def test_edition_uses_original_time_in_hong_kong(self) -> None:
        draft = {"items": [{"ordinal": "一", "headline": "测试", "content": "成绩。\n\n评价。"}]}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "generator.images.render_digest_summary_card", wraps=render_digest_summary_card
        ) as cover, patch(
            "generator.images.render_item_card_with_measure", wraps=render_item_card_with_measure
        ) as card:
            generate_images_for_digest(
                draft, [], Path(tmp) / "drafts/digest", {},
                generated_at=datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc),
            )
            self.assertEqual(cover.call_args.kwargs["footer_label"], "2026-09-25 07:30 HKT")
            self.assertEqual(card.call_args.kwargs["footer_label"], "2026-09-25 07:30 HKT")
            measurement = json.loads((Path(tmp) / "drafts/digest/render_measurements.json").read_text())
            self.assertEqual(measurement["truncated_count"], 0)
            self.assertEqual(measurement["items"][0]["source_chars"], 6)

    def test_text_width_is_reduced_to_encourage_more_line_breaks(self) -> None:
        self.assertEqual(TEXT_WIDTH_RATIO, 0.9)

    def test_digest_images_are_topic_text_cards_only(self) -> None:
        draft = {
            "title": "围场过去24H新闻",
            "hook": "导语",
            "items": [
                {"ordinal": "一", "headline": "标题一", "content": "正文一" * 30},
                {"ordinal": "二", "headline": "标题二", "content": "正文二" * 30},
            ],
            "sources": ["https://example.com/source"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts" / "digest"
            paths = generate_images_for_digest(
                draft,
                [],
                draft_dir,
                {"images": {"width": 540, "height": 720, "fetch_og_image": True}},
            )

            names = [Path(path).name for path in paths]

        self.assertEqual(names, ["cover.png", "slide_01.png", "slide_02.png"])

    def test_short_content_reports_no_truncation(self) -> None:
        content = "法拉利在西班牙站带来新底板，目标是改善低速弯的下压力表现。"

        _, measure = render_item_card_with_measure("一", "底板升级", content, 540, 720)

        self.assertFalse(measure["truncated"])
        self.assertEqual(measure["rendered_chars"], measure["source_chars"])
        self.assertEqual(measure["source_chars"], len(content))

    def test_oversized_content_is_reported_as_truncated(self) -> None:
        # The old code silently dropped lines here; the loss must be measurable.
        content = "这是一条非常长的正文。" * 400

        _, measure = render_item_card_with_measure("一", "超长", content, 540, 720)

        self.assertTrue(measure["truncated"], "oversized content must be flagged")
        self.assertLess(measure["rendered_chars"], measure["source_chars"])

    def test_measurements_are_written_next_to_the_images(self) -> None:
        draft = {
            "title": "围场过去24H新闻",
            "hook": "导语",
            "items": [
                {"ordinal": "一", "headline": "短标题", "content": "短正文。"},
                {"ordinal": "二", "headline": "长标题", "content": "很长的正文。" * 400},
            ],
            "sources": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts" / "digest"
            generate_images_for_digest(
                draft, [], draft_dir, {"images": {"width": 540, "height": 720}}
            )
            payload = json.loads(
                (draft_dir / RENDER_MEASUREMENTS_FILENAME).read_text(encoding="utf-8")
            )

        self.assertEqual(payload["truncated_count"], 1)
        self.assertEqual([item["slide"] for item in payload["items"]], ["slide_01.png", "slide_02.png"])
        self.assertFalse(payload["items"][0]["truncated"])
        self.assertTrue(payload["items"][1]["truncated"])


if __name__ == "__main__":
    unittest.main()

    def test_rendered_chars_matches_lines_actually_drawn(self) -> None:
        """R8: the reported figure must not count lines the draw loop skipped."""
        content = "很长的正文内容。" * 300

        _, measure = render_item_card_with_measure("一", "标题", content, 1080, 710)

        # Reproduction from the GPT-6 review: at this size the draw loop skips a
        # line, and the old code still counted it as rendered.
        self.assertGreaterEqual(measure["dropped_lines"], 1)
        self.assertTrue(measure["truncated"])
        self.assertLess(measure["rendered_chars"], measure["source_chars"])

    def test_truncated_flag_tracks_dropped_lines(self) -> None:
        short = "短正文。"
        _, measure = render_item_card_with_measure("一", "标题", short, 1080, 1440)

        self.assertEqual(measure["dropped_lines"], 0)
        self.assertFalse(measure["truncated"])
