from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publisher.telegram import _digest_title_for_telegram
from publisher.telegram import _format_digest_text
from publisher.telegram import _send_media_groups
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
        self.assertEqual(result["text_chars"], len("围场过去24H新闻26.07.13"))
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
        self.assertNotIn("正文", text)
        self.assertNotIn("#F1", text)
        self.assertNotIn("Sources", text)
        self.assertNotIn("https://example.com/source", text)

    def test_text_is_trimmed_to_max_chars(self) -> None:
        text = _format_digest_text(
            {"title": "围场过去24H新闻" * 40, "items": []},
            max_chars=20,
            title="围场过去24H新闻" * 40,
        )

        self.assertLessEqual(len(text), 20)
        self.assertTrue(text.endswith("…"))

    def test_telegram_title_uses_meta_generation_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts" / "digest"
            draft_dir.mkdir(parents=True)
            (draft_dir / "meta.json").write_text(
                json.dumps({"run_context": {"generated_at": "2026-07-13T09:30:00+00:00"}}),
                encoding="utf-8",
            )

            title = _digest_title_for_telegram(draft_dir, "围场过去24H新闻")

        self.assertEqual(title, "围场过去24H新闻26.07.13")

    def test_media_groups_split_after_ten_images(self) -> None:
        images = [Path(f"slide_{index:02d}.png") for index in range(11)]
        with patch("publisher.telegram._send_media_group", return_value={"ok": True}) as send:
            result = _send_media_groups("token", "chat", images, 30)

        self.assertEqual(result, [{"ok": True}, {"ok": True}])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(send.call_args_list[0].args[2]), 10)
        self.assertEqual(len(send.call_args_list[1].args[2]), 1)

if __name__ == "__main__":
    unittest.main()
