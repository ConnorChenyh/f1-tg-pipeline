from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from publisher.telegram import push_digest_to_telegram

logger = logging.getLogger(__name__)


def _queue_path(root: Path, config: dict[str, Any]) -> Path:
    value = config.get("telegram", {}).get(
        "pending_deliveries_path", "output/pending_telegram_deliveries.json"
    )
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_queue(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        deliveries = data.get("deliveries", [])
        if isinstance(deliveries, list):
            return [entry for entry in deliveries if isinstance(entry, dict) and entry.get("output_dir")]
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Unable to read pending Telegram deliveries: %s", type(exc).__name__)
    return []


def _save_queue(path: Path, deliveries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps({"deliveries": deliveries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def enqueue_pending_delivery(root: Path, config: dict[str, Any], output_dir: Path) -> None:
    relative_output_dir = output_dir.resolve().relative_to(root.resolve())
    entry = {"output_dir": str(relative_output_dir), "queued_at": datetime.now(timezone.utc).isoformat()}
    path = _queue_path(root, config)
    deliveries = _load_queue(path)
    if not any(existing["output_dir"] == entry["output_dir"] for existing in deliveries):
        deliveries.append(entry)
        _save_queue(path, deliveries)
        logger.warning("Queued digest for Telegram compensation: %s", relative_output_dir)


def deliver_pending_digests(
    root: Path,
    config: dict[str, Any],
    *,
    send: Callable[..., dict[str, Any]] = push_digest_to_telegram,
) -> int:
    path = _queue_path(root, config)
    deliveries = _load_queue(path)
    delivered = 0
    remaining: list[dict[str, str]] = []
    for entry in deliveries:
        output_dir = (root / entry["output_dir"]).resolve()
        try:
            output_dir.relative_to((root / "output").resolve())
            send(output_dir / "drafts" / "digest", config)
            delivered += 1
            logger.info("Compensated pending Telegram digest: %s", entry["output_dir"])
        except Exception as exc:
            remaining.append(entry)
            logger.warning(
                "Pending Telegram compensation failed for %s: %s",
                entry["output_dir"],
                type(exc).__name__,
            )
    if deliveries:
        _save_queue(path, remaining)
    return delivered
