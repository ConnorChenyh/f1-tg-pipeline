from __future__ import annotations

import json
import hashlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from PIL import Image
from analyzer.file_state import write_json_atomic

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


def _digest_title_for_telegram(draft_dir: Path | None = None, title: str | None = None) -> str:
    """Build the delivered title from the run's stored context.

    ``title`` is the digest title the pipeline wrote. The generation date comes
    from ``meta.json`` when available, so the date suffix has one source of
    truth instead of being recomputed at delivery time.
    """
    base = (title or "").strip()
    generated_at: datetime | None = None
    if draft_dir is not None:
        meta_path = draft_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = _load_json(meta_path)
                if not base:
                    base = str(meta.get("digest_title") or "").strip()
                raw = (meta.get("run_context") or {}).get("generated_at")
                if raw:
                    generated_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone()
            except Exception as exc:
                logger.info("Failed to read Telegram title context from %s: %s", meta_path, exc)

    if generated_at is None:
        generated_at = datetime.now().astimezone()

    prefix = base or "围场过去24H新闻"
    return f"{prefix}{generated_at.strftime('%y.%m.%d')}"


def _format_digest_text(
    draft: dict[str, Any],
    max_chars: int = DEFAULT_MAX_TEXT_CHARS,
    title: str | None = None,
) -> str:
    """Deliver the title line only, followed by the image set.

    The digest body is not duplicated into the message: the cover card lists the
    topics and each detail card carries its own text.
    """
    text = title or str(draft.get("title") or "")
    return _trim_text(text, min(max_chars, TELEGRAM_MESSAGE_LIMIT))


def _telegram_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _post_with_retry(
    request: Callable[[], requests.Response],
    *,
    method: str,
    attempts: int,
    backoff_sec: float,
) -> requests.Response:
    for attempt in range(1, attempts + 1):
        try:
            response = request()
            status = getattr(response, "status_code", 200)
            if isinstance(status, int) and (status == 429 or status >= 500) and attempt < attempts:
                delay = backoff_sec * attempt
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after")
                    if retry_after is not None:
                        delay = max(delay, float(retry_after))
                except (ValueError, TypeError, AttributeError):
                    pass
                logger.warning("Telegram %s HTTP %s; retrying in %.1fs", method, status, delay)
                time.sleep(delay)
                continue
            return response
        except requests.RequestException as exc:
            if attempt == attempts:
                raise RuntimeError(
                    f"Telegram {method} network request failed after {attempts} attempts: "
                    f"{type(exc).__name__}"
                ) from exc
            delay = backoff_sec * attempt
            logger.warning(
                "Telegram %s network request failed (attempt %d/%d); retrying in %.1fs: %s",
                method,
                attempt,
                attempts,
                delay,
                type(exc).__name__,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def _post_telegram_json(
    token: str,
    method: str,
    payload: dict[str, Any],
    timeout_sec: int,
    retry_attempts: int,
    retry_backoff_sec: float,
) -> dict[str, Any]:
    response = _post_with_retry(
        lambda: requests.post(
            _telegram_api_url(token, method),
            json=payload,
            timeout=timeout_sec,
        ),
        method=method,
        attempts=retry_attempts,
        backoff_sec=retry_backoff_sec,
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
    retry_attempts: int,
    retry_backoff_sec: float,
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

        method = "sendPhoto" if len(images) == 1 else "sendMediaGroup"
        def send_request() -> requests.Response:
            for handle in handles:
                handle.seek(0)
            return requests.post(
                _telegram_api_url(token, method),
                data={"chat_id": chat_id} if len(images) == 1 else {"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
                files={"photo": files["photo0"]} if len(images) == 1 else files,
                timeout=timeout_sec,
            )

        response = _post_with_retry(
            send_request,
            method=method,
            attempts=retry_attempts,
            backoff_sec=retry_backoff_sec,
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


def _send_media_groups(
    token: str,
    chat_id: str,
    images: list[Path],
    timeout_sec: int,
    retry_attempts: int = 1,
    retry_backoff_sec: float = 0,
) -> list[dict[str, Any]]:
    return [
        result
        for start in range(0, len(images), TELEGRAM_MEDIA_GROUP_LIMIT)
        if (result := _send_media_group(
            token,
            chat_id,
            images[start : start + TELEGRAM_MEDIA_GROUP_LIMIT],
            timeout_sec,
            retry_attempts,
            retry_backoff_sec,
        )) is not None
    ]


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
    retry_attempts = max(1, int(telegram_cfg.get("retry_attempts", 3)))
    retry_backoff_sec = max(0, float(telegram_cfg.get("retry_backoff_seconds", 5)))
    max_text_chars = int(telegram_cfg.get("max_text_chars", DEFAULT_MAX_TEXT_CHARS))

    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        raise TelegramConfigError("TELEGRAM_CHAT_ID is not set")

    draft_path = draft_dir / "draft.json"
    if not draft_path.exists():
        raise FileNotFoundError(f"draft.json not found: {draft_path}")

    draft = _load_json(draft_path)
    title = str(draft.get("telegram_title") or _digest_title_for_telegram(draft_dir, draft.get("title")))
    text = _format_digest_text(draft, max_chars=max_text_chars, title=title)
    items = draft.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Digest must have at least one item before delivery")
    images = [draft_dir / "images" / "cover.png"] + [
        draft_dir / "images" / f"slide_{index:02d}.png" for index in range(1, len(items) + 1)
    ]
    for path in images:
        with Image.open(path) as image:
            image.verify()
    measurements = draft_dir / "render_measurements.json"
    if measurements.exists() and _load_json(measurements).get("truncated_count", 0):
        raise ValueError("Digest contains truncated images; regenerate before delivery")
    meta_path = draft_dir / "meta.json"
    if meta_path.exists() and _load_json(meta_path).get("guard_blocked"):
        raise ValueError("Digest is blocked by quality checks; repair and revalidate before delivery")

    if dry_run:
        return {
            "dry_run": True,
            "chat_id": chat_id,
            "text_chars": len(text),
            "text_preview": text[:120],
            "images": [str(path) for path in images],
        }

    fingerprint = hashlib.sha256()
    fingerprint.update(json.dumps([chat_id, token.split(":", 1)[0], text], ensure_ascii=False).encode())
    for path in images:
        fingerprint.update(path.name.encode())
        fingerprint.update(path.read_bytes())
    key = fingerprint.hexdigest()
    checkpoint_path = draft_dir / "telegram_delivery.json"
    checkpoint = {"fingerprint": key, "media": []}
    if checkpoint_path.exists():
        checkpoint = _load_json(checkpoint_path)
        if checkpoint.get("fingerprint") != key:
            raise ValueError("Delivered payload changed; use a new run directory for a revised digest")
        if (not isinstance(checkpoint.get("media"), list)
                or any(not isinstance(item, dict) or item.get("ok") is not True
                       for item in checkpoint["media"])
                or ("message" in checkpoint and (not isinstance(checkpoint["message"], dict)
                    or checkpoint["message"].get("ok") is not True))):
            raise ValueError("Invalid Telegram delivery checkpoint")

    if "message" not in checkpoint:
        checkpoint["message"] = _post_telegram_json(
            token, "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout_sec, retry_attempts, retry_backoff_sec,
        )
        write_json_atomic(checkpoint_path, checkpoint)
    message_result = checkpoint["message"]
    media_results = checkpoint["media"]
    batches = [images[start:start + TELEGRAM_MEDIA_GROUP_LIMIT]
               for start in range(0, len(images), TELEGRAM_MEDIA_GROUP_LIMIT)]
    if len(media_results) > len(batches):
        raise ValueError("Invalid Telegram delivery batch count")
    for batch in batches[len(media_results):]:
        result = _send_media_group(token, chat_id, batch, timeout_sec, retry_attempts, retry_backoff_sec)
        media_results.append(result)
        write_json_atomic(checkpoint_path, checkpoint)

    logger.info(
        "Telegram push complete: %d images in %d media group(s)",
        len(images),
        len(media_results),
    )
    return {
        "message": message_result,
        "media": media_results,
        "media_group_count": len(media_results),
        "image_count": len(images),
    }
