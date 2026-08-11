from __future__ import annotations

import os
from typing import Any

from publisher.telegram import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramConfigError,
    _post_telegram_json,
    _trim_text,
)


def send_text_to_telegram(
    text: str,
    config: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    telegram_cfg = config.get("telegram", {})
    token = os.getenv("TELEGRAM_BOT_TOKEN") or telegram_cfg.get("bot_token")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or telegram_cfg.get("chat_id")
    timeout_sec = int(telegram_cfg.get("timeout_sec", 30))

    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        raise TelegramConfigError("TELEGRAM_CHAT_ID is not set")
    if dry_run:
        return {"dry_run": True, "chat_id": chat_id, "text": text}
    return _post_telegram_json(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": _trim_text(text, TELEGRAM_MESSAGE_LIMIT),
            "disable_web_page_preview": True,
        },
        timeout_sec,
    )
