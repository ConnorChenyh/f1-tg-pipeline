from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_MEDIA_GROUP_LIMIT = 10
DEFAULT_MAX_TEXT_CHARS = 1000


class TelegramConfigError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _digest_title_for_telegram(draft_dir: Path | None = None) -> str:
    generated_at: datetime | None = None
    if draft_dir is not None:
        meta_path = draft_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = _load_json(meta_path)
                raw = (meta.get("run_context") or {}).get("generated_at")
                if raw:
                    generated_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone()
            except Exception as exc:
                logger.info("Failed to read Telegram title date from %s: %s", meta_path, exc)

    if generated_at is None:
        generated_at = datetime.now().astimezone()

    return f"围场过去24H新闻{generated_at.strftime('%y.%m.%d')}"


def _format_digest_text(
    draft: dict[str, Any],
    max_chars: int = DEFAULT_MAX_TEXT_CHARS,
    title: str | None = None,
) -> str:
    text = title or str(draft.get("title") or "")
    return _trim_text(text, min(max_chars, TELEGRAM_MESSAGE_LIMIT))


def _image_paths(draft_dir: Path) -> list[Path]:
    images_dir = draft_dir / "images"
    if not images_dir.exists():
        return []

    preferred = ["cover.png"]
    preferred.extend(f"slide_{index:02d}.png" for index in range(1, 10))

    ordered: list[Path] = []
    seen: set[Path] = set()
    for name in preferred:
        path = images_dir / name
        if path.exists():
            ordered.append(path)
            seen.add(path)

    for path in sorted(images_dir.glob("*.png")):
        if path not in seen and path.name != "slide_last.png":
            ordered.append(path)

    return ordered[:TELEGRAM_MEDIA_GROUP_LIMIT]


def _telegram_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _post_telegram_json(token: str, method: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    response = requests.post(
        _telegram_api_url(token, method),
        json=payload,
        timeout=timeout_sec,
    )
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError(f"Telegram {method} returned non-JSON response")

    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data


def _send_media_group(
    token: str,
    chat_id: str,
    images: list[Path],
    timeout_sec: int,
) -> dict[str, Any] | None:
    if not images:
        return None

    media: list[dict[str, str]] = []
    files: dict[str, Any] = {}
    handles = []
    try:
        for index, image_path in enumerate(images):
            field = f"photo{index}"
            media.append({"type": "photo", "media": f"attach://{field}"})
            handle = image_path.open("rb")
            handles.append(handle)
            files[field] = (image_path.name, handle, "image/png")

        response = requests.post(
            _telegram_api_url(token, "sendMediaGroup"),
            data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
            files=files,
            timeout=timeout_sec,
        )
        try:
            data = response.json()
        except ValueError:
            response.raise_for_status()
            raise RuntimeError("Telegram sendMediaGroup returned non-JSON response")

        if response.status_code >= 400 or not data.get("ok"):
            raise RuntimeError(f"Telegram sendMediaGroup failed: {data}")
        return data
    finally:
        for handle in handles:
            handle.close()


def push_digest_to_telegram(
    draft_dir: Path,
    config: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    telegram_cfg = config.get("telegram", {})
    token = os.getenv("TELEGRAM_BOT_TOKEN") or telegram_cfg.get("bot_token")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or telegram_cfg.get("chat_id")
    timeout_sec = int(telegram_cfg.get("timeout_sec", 30))
    max_text_chars = int(telegram_cfg.get("max_text_chars", DEFAULT_MAX_TEXT_CHARS))

    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        raise TelegramConfigError("TELEGRAM_CHAT_ID is not set")

    draft_path = draft_dir / "draft.json"
    if not draft_path.exists():
        raise FileNotFoundError(f"draft.json not found: {draft_path}")

    draft = _load_json(draft_path)
    title = _digest_title_for_telegram(draft_dir)
    text = _format_digest_text(draft, max_chars=max_text_chars, title=title)
    images = _image_paths(draft_dir)

    if dry_run:
        return {
            "dry_run": True,
            "chat_id": chat_id,
            "text_chars": len(text),
            "text_preview": text[:120],
            "images": [str(path) for path in images],
        }

    message_result = _post_telegram_json(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout_sec,
    )
    media_result = _send_media_group(token, chat_id, images, timeout_sec)

    logger.info("Telegram push complete: %d images", len(images))
    return {
        "message": message_result,
        "media": media_result,
        "image_count": len(images),
    }
