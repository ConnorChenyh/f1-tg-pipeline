from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from generator.images import TEXT_WIDTH_RATIO, _load_font, _wrap_text, generate_images_for_digest


class DigestImageTests(unittest.TestCase):
    def test_wrap_text_keeps_english_words_intact(self) -> None:
        draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
        font = _load_font(30)
        word_width = draw.textbbox((0, 0), "McLaren", font=font)[2]

        lines = _wrap_text(draw, "前McLaren后 Mercedes-AMG", font, word_width - 1)

        self.assertEqual(lines, ["前", "McLaren", "后", "Mercedes-AMG"])

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


if __name__ == "__main__":
    unittest.main()
