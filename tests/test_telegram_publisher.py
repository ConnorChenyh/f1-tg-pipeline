from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publisher.telegram import _format_digest_text
from publisher.telegram import push_digest_to_telegram


class TelegramPublisherTests(unittest.TestCase):
    def test_dry_run_builds_text_and_image_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts" / "digest"
            images_dir = draft_dir / "images"
            images_dir.mkdir(parents=True)
            (images_dir / "cover.png").write_bytes(b"png")
            (images_dir / "slide_01.png").write_bytes(b"png")
            (images_dir / "slide_02.png").write_bytes(b"png")
            (images_dir / "slide_last.png").write_bytes(b"png")
            (draft_dir / "draft.json").write_text(
                json.dumps(
                    {
                        "title": "围场过去24H新闻",
                        "hook": "一句总起",
                        "items": [
                            {
                                "ordinal": "一",
                                "headline": "标题",
                                "content": "正文" * 800,
                            }
                        ],
                        "hashtags": ["#F1"],
                        "sources": ["https://example.com/f1"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (draft_dir / "meta.json").write_text(
                json.dumps({"run_context": {"generated_at": "2026-07-13T09:30:00+00:00"}}),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "test-token",
                    "TELEGRAM_CHAT_ID": "123",
                },
            ):
                result = push_digest_to_telegram(draft_dir, {"telegram": {}}, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["chat_id"], "123")
        self.assertEqual(result["text_preview"], "围场过去24H新闻26.07.13")
        self.assertEqual(len(result["images"]), 3)
        self.assertTrue(result["images"][0].endswith("cover.png"))
        self.assertTrue(result["images"][1].endswith("slide_01.png"))
        self.assertTrue(result["images"][2].endswith("slide_02.png"))
        self.assertFalse(any(path.endswith("slide_last.png") for path in result["images"]))

    def test_text_is_fixed_title_only(self) -> None:
        text = _format_digest_text(
            {
                "title": "围场过去24H新闻",
                "hook": "导语不要出现",
                "items": [
                    {
                        "ordinal": "一",
                        "headline": "标题",
                        "content": "正文",
                    }
                ],
                "hashtags": ["#F1"],
                "sources": ["https://example.com/source"],
            },
            max_chars=1000,
            title="围场过去24H新闻26.07.13",
        )

        self.assertEqual(text, "围场过去24H新闻26.07.13")
        self.assertNotIn("导语不要出现", text)
        self.assertNotIn("一、标题", text)
        self.assertNotIn("#F1", text)
        self.assertNotIn("Sources", text)
        self.assertNotIn("https://example.com/source", text)


if __name__ == "__main__":
    unittest.main()
