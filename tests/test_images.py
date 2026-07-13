from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generator.images import generate_images_for_digest


class DigestImageTests(unittest.TestCase):
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
