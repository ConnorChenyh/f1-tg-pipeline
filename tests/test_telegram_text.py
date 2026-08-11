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


if __name__ == "__main__":
    unittest.main()
