from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from publisher.telegram_text import send_text_to_telegram


class TelegramTextTests(unittest.TestCase):
    def test_dry_run_uses_existing_telegram_config(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "123"},
        ):
            result = send_text_to_telegram("赛季背景已更新", {"telegram": {}}, dry_run=True)

        self.assertEqual("123", result["chat_id"])
        self.assertEqual("赛季背景已更新", result["text"])

    def test_send_text_uses_telegram_retry_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "123"},
        ), patch("publisher.telegram_text._post_telegram_json", return_value={"ok": True}) as send:
            result = send_text_to_telegram(
                "赛季背景已更新",
                {"telegram": {"retry_attempts": 4, "retry_backoff_seconds": 2.5}},
            )

        self.assertEqual({"ok": True}, result)
        send.assert_called_once_with(
            "test-token",
            "sendMessage",
            {
                "chat_id": "123",
                "text": "赛季背景已更新",
                "disable_web_page_preview": True,
            },
            30,
            4,
            2.5,
        )


if __name__ == "__main__":
    unittest.main()
