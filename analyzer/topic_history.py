from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from generator.evidence_pack import normalize_source_url

logger = logging.getLogger(__name__)

TEXT_RE = re.compile(r"\s+")


def _history_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("topic_history", {}) or {}


def history_enabled(config: dict[str, Any]) -> bool:
    return bool(_history_config(config).get("enabled", True))


def history_path(root: Path, config: dict[str, Any]) -> Path:
    configured = _history_config(config).get("path", "output/topic_history.json")
    path = Path(configured)
    return path if path.is_absolute() else root / path


def history_days(config: dict[str, Any]) -> int:
    return int(_history_config(config).get("dedupe_days", 7))


def similarity_threshold(config: dict[str, Any]) -> float:
    return float(_history_config(config).get("similarity_threshold", 0.72))


def backfill_max_age_hours(config: dict[str, Any]) -> float:
    return float(_history_config(config).get("backfill_max_age_hours", 6))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read topic history %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _prune_history(
    entries: list[dict[str, Any]],
    now: datetime,
    dedupe_days: int,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=dedupe_days)
    kept: list[dict[str, Any]] = []
    for entry in entries:
        published_at = _parse_datetime(str(entry.get("published_at") or ""))
        if published_at is None or published_at >= cutoff:
            kept.append(entry)
    return kept


def _topic_text(topic: dict[str, Any]) -> str:
    raw = " ".join(
        str(topic.get(key) or "")
        for key in ("title_zh", "summary")
    )
    return TEXT_RE.sub(" ", raw.lower()).strip()


def _topic_urls(topic: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for url in topic.get("evidence_urls", []) or []:
        normalized = normalize_source_url(str(url))
        if normalized:
            urls.add(normalized)
    for post in topic.get("evidence_posts", []) or []:
        if not isinstance(post, dict):
            continue
        normalized = normalize_source_url(str(post.get("url") or ""))
        if normalized:
            urls.add(normalized)
    return urls


def _match_reason(
    topic: dict[str, Any],
    entry: dict[str, Any],
    threshold: float,
) -> str | None:
    shared_urls = _topic_urls(topic) & set(str(url) for url in entry.get("evidence_urls", []) or [])
    if shared_urls:
        return f"shared_url:{next(iter(shared_urls))}"

    topic_text = _topic_text(topic)
    entry_text = TEXT_RE.sub(
        " ",
        f"{entry.get('title_zh') or ''} {entry.get('summary') or ''}".lower(),
    ).strip()
    if topic_text and entry_text:
        ratio = SequenceMatcher(None, topic_text, entry_text).ratio()
        if ratio >= threshold:
            return f"text_similarity:{ratio:.2f}"
    return None


def filter_recent_topics(
    topics: list[dict[str, Any]],
    root: Path,
    config: dict[str, Any],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not history_enabled(config):
        return topics, []

    path = history_path(root, config)
    entries = _prune_history(_load_history(path), now, history_days(config))
    threshold = similarity_threshold(config)

    fresh: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for topic in topics:
        reason = None
        matched_entry = None
        for entry in entries:
            reason = _match_reason(topic, entry, threshold)
            if reason:
                matched_entry = entry
                break
        if reason:
            duplicate_published_at = _parse_datetime(str((matched_entry or {}).get("published_at") or ""))
            duplicate_age_hours = None
            if duplicate_published_at:
                duplicate_age_hours = max((now - duplicate_published_at).total_seconds() / 3600, 0.0)
            skipped.append(
                {
                    "id": topic.get("id"),
                    "title_zh": topic.get("title_zh"),
                    "reason": reason,
                    "duplicate_published_at": duplicate_published_at.isoformat() if duplicate_published_at else None,
                    "duplicate_age_hours": round(duplicate_age_hours, 2) if duplicate_age_hours is not None else None,
                }
            )
        else:
            fresh.append(topic)

    if skipped:
        logger.info("Topic history skipped %d repeated topics", len(skipped))
    return fresh, skipped


def append_topic_history(
    topics: list[dict[str, Any]],
    root: Path,
    config: dict[str, Any],
    now: datetime,
) -> None:
    if not history_enabled(config):
        return

    path = history_path(root, config)
    entries = _prune_history(_load_history(path), now, history_days(config))
    for topic in topics:
        entries.append(
            {
                "published_at": now.isoformat(),
                "id": topic.get("id"),
                "title_zh": topic.get("title_zh"),
                "summary": topic.get("summary"),
                "evidence_urls": sorted(_topic_urls(topic)),
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Topic history updated: %s (%d entries)", path, len(entries))
