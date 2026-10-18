from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from collectors.base import PostItem
from generator.evidence_pack import normalize_source_url
from analyzer.file_state import write_json_atomic
from analyzer.topic_signature import topic_signature, topic_signature_cooldown_days

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
        published_at = _parse_datetime(str(entry.get("recorded_at") or entry.get("published_at") or ""))
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
    config: dict[str, Any],
    now: datetime,
    threshold: float,
) -> str | None:
    if entry.get("outcome", "delivered") != "delivered":
        recorded_at = _parse_datetime(str(entry.get("recorded_at") or ""))
        cooldown = float(_history_config(config).get("unpublished_cooldown_hours", 1))
        if recorded_at is None or now - recorded_at > timedelta(hours=cooldown):
            return None
    current_signature = topic_signature(topic, config)
    entry_signature = str(entry.get("topic_signature") or "") or topic_signature(entry, config)
    if current_signature and entry_signature and current_signature == entry_signature:
        cooldown_days = topic_signature_cooldown_days(current_signature, config)
        if cooldown_days is None:
            return f"topic_signature:{current_signature}"
        published_at = _parse_datetime(str(entry.get("recorded_at") or entry.get("published_at") or ""))
        if published_at and now - published_at <= timedelta(days=cooldown_days):
            return f"topic_signature:{current_signature}"

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
        # A theme cooldown must win even if an earlier record shares a URL.
        for entry in reversed(entries):
            candidate_reason = _match_reason(topic, entry, config, now, threshold)
            if candidate_reason and (reason is None or candidate_reason.startswith("topic_signature:")):
                reason, matched_entry = candidate_reason, entry
            if reason and reason.startswith("topic_signature:"):
                break
        if reason:
            duplicate_published_at = _parse_datetime(str((matched_entry or {}).get("recorded_at") or (matched_entry or {}).get("published_at") or ""))
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
    outcome: str = "delivered",
) -> None:
    if not history_enabled(config):
        return

    path = history_path(root, config)
    entries = _prune_history(_load_history(path), now, history_days(config))
    for topic in topics:
        urls = sorted(_topic_urls(topic))
        entries = [entry for entry in entries if not (
            (entry.get("recorded_at") or entry.get("published_at")) == now.isoformat()
            and entry.get("id") == topic.get("id") and entry.get("evidence_urls") == urls
        )]
        entries.append(
            {
                "recorded_at": now.isoformat(),
                "published_at": now.isoformat() if outcome == "delivered" else None,
                "outcome": outcome,
                "id": topic.get("id"),
                "title_zh": topic.get("title_zh"),
                "summary": topic.get("summary"),
                "topic_signature": topic_signature(topic, config),
                "evidence_urls": sorted(_topic_urls(topic)),
            }
        )

    write_json_atomic(path, entries)
    logger.info("Topic history updated: %s (%d entries)", path, len(entries))


def _delivered_entries(
    root: Path,
    config: dict[str, Any],
    now: datetime,
    days: float,
) -> list[dict[str, Any]]:
    if not history_enabled(config):
        return []
    cutoff = now - timedelta(days=days)
    entries = []
    for entry in _load_history(history_path(root, config)):
        if entry.get("outcome", "delivered") != "delivered":
            continue
        published_at = _parse_datetime(str(entry.get("published_at") or entry.get("recorded_at") or ""))
        if published_at is not None and cutoff <= published_at <= now:
            entries.append(entry)
    return entries


def published_urls(root: Path, config: dict[str, Any], now: datetime) -> set[str]:
    """Normalized evidence URLs of topics delivered within the dedupe window."""
    urls: set[str] = set()
    for entry in _delivered_entries(root, config, now, history_days(config)):
        urls.update(str(url) for url in entry.get("evidence_urls", []) or [] if url)
    return urls


def recent_published_topics(
    root: Path,
    config: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Delivered topics shown to topic extraction so follow-ups need news."""
    days = float(_history_config(config).get("follow_up_context_days", 7))
    if days <= 0:
        return []
    entries = _delivered_entries(root, config, now, days)
    entries.sort(key=lambda entry: str(entry.get("published_at") or entry.get("recorded_at") or ""))
    return [
        {
            "published_at": entry.get("published_at") or entry.get("recorded_at"),
            "title_zh": entry.get("title_zh"),
            "summary": entry.get("summary"),
        }
        for entry in entries
    ]


def drop_published_posts(posts: list[Any], urls: set[str]) -> tuple[list[Any], list[str]]:
    """Remove already-published articles before topic extraction.

    Feeds re-publish old articles with fresh timestamps; if one reaches the
    model it gets clustered into an otherwise new topic, and the shared_url
    history check then drops the whole topic. Related posts are filtered too;
    when a cluster's lead post is old, its first unpublished member leads.
    """
    if not urls:
        return list(posts), []
    kept: list[Any] = []
    removed: list[str] = []
    for post in posts:
        related = list(post.extra.get("related_posts", []) or [])
        fresh_related = []
        for item in related:
            if normalize_source_url(str(item.get("url") or "")) in urls:
                removed.append(str(item.get("url")))
            else:
                fresh_related.append(item)
        if normalize_source_url(post.url) in urls:
            removed.append(post.url)
            if not fresh_related:
                continue
            lead = PostItem.from_dict(fresh_related[0])
            lead.extra = {**lead.extra, "related_posts": fresh_related[1:]}
            kept.append(lead)
            continue
        if len(fresh_related) != len(related):
            post = PostItem.from_dict(post.to_dict())
            post.extra["related_posts"] = fresh_related
        kept.append(post)
    if removed:
        logger.info("Removed %d already-published URLs before topic extraction", len(removed))
    return kept, removed
