from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from collectors.base import PostItem
from generator.evidence_pack import collect_evidence_source_urls, normalize_source_url

logger = logging.getLogger(__name__)


def _db_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("story_db", {}) or {}


def story_db_enabled(config: dict[str, Any]) -> bool:
    return bool(_db_config(config).get("enabled", True))


def story_db_path(root: Path, config: dict[str, Any]) -> Path:
    configured = _db_config(config).get("path", "output/story_memory.sqlite3")
    path = Path(configured)
    return path if path.is_absolute() else root / path


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_story_db(root: Path, config: dict[str, Any]) -> None:
    if not story_db_enabled(config):
        return
    path = story_db_path(root, config)
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                source TEXT NOT NULL,
                feed_name TEXT,
                title TEXT,
                url TEXT,
                canonical_url TEXT,
                created_at TEXT,
                raw_score REAL NOT NULL DEFAULT 0,
                shortlist_score REAL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_url ON candidates(canonical_url);
            CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id);

            CREATE TABLE IF NOT EXISTS published_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                published_at TEXT NOT NULL,
                topic_id TEXT,
                title_zh TEXT,
                summary TEXT,
                fingerprint TEXT NOT NULL,
                evidence_urls_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_published_at ON published_topics(published_at);
            CREATE INDEX IF NOT EXISTS idx_published_fingerprint ON published_topics(fingerprint);
            """
        )
    logger.info("Story DB ready: %s", path)


def _topic_fingerprint(topic: dict[str, Any]) -> str:
    urls = sorted(collect_evidence_source_urls([topic]))
    if urls:
        return "url:" + "|".join(urls[:3])
    text = " ".join(str(topic.get(key) or "") for key in ("title_zh", "summary")).strip().lower()
    return "text:" + text[:240]


def record_candidates(
    posts: list[PostItem],
    root: Path,
    config: dict[str, Any],
    run_id: str,
    collected_at: datetime,
) -> None:
    if not story_db_enabled(config):
        return

    path = story_db_path(root, config)
    rows = []
    for post in posts:
        rows.append(
            (
                run_id,
                collected_at.isoformat(),
                post.source,
                post.feed_name,
                post.title,
                post.url,
                normalize_source_url(post.url),
                post.created_at.isoformat(),
                float(post.raw_score or 0.0),
                float(post.extra.get("shortlist_score")) if post.extra.get("shortlist_score") is not None else None,
                json.dumps(post.to_dict(), ensure_ascii=False),
            )
        )

    with _connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO candidates (
                run_id, collected_at, source, feed_name, title, url, canonical_url,
                created_at, raw_score, shortlist_score, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    logger.info("Story DB recorded %d candidates", len(rows))


def record_published_topics(
    topics: list[dict[str, Any]],
    root: Path,
    config: dict[str, Any],
    published_at: datetime,
) -> None:
    if not story_db_enabled(config):
        return

    path = story_db_path(root, config)
    rows = []
    for topic in topics:
        urls = sorted(collect_evidence_source_urls([topic]))
        rows.append(
            (
                published_at.isoformat(),
                str(topic.get("id") or ""),
                str(topic.get("title_zh") or ""),
                str(topic.get("summary") or ""),
                _topic_fingerprint(topic),
                json.dumps(urls, ensure_ascii=False),
                json.dumps(topic, ensure_ascii=False),
            )
        )

    with _connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO published_topics (
                published_at, topic_id, title_zh, summary, fingerprint,
                evidence_urls_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    logger.info("Story DB recorded %d published topics", len(rows))


def prune_story_db(root: Path, config: dict[str, Any], now: datetime) -> None:
    if not story_db_enabled(config):
        return

    retention_days = int(_db_config(config).get("retention_days", 30))
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    path = story_db_path(root, config)
    with _connect(path) as conn:
        conn.execute("DELETE FROM candidates WHERE collected_at < ?", (cutoff,))
        conn.execute("DELETE FROM published_topics WHERE published_at < ?", (cutoff,))


def recent_published_fingerprints(root: Path, config: dict[str, Any], now: datetime) -> set[str]:
    if not story_db_enabled(config):
        return set()

    dedupe_days = int(config.get("topic_history", {}).get("dedupe_days", 7))
    cutoff = (now - timedelta(days=dedupe_days)).isoformat()
    path = story_db_path(root, config)
    if not path.exists():
        return set()

    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT fingerprint FROM published_topics WHERE published_at >= ?",
            (cutoff,),
        ).fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def filter_topics_seen_in_story_db(
    topics: list[dict[str, Any]],
    root: Path,
    config: dict[str, Any],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fingerprints = recent_published_fingerprints(root, config, now)
    if not fingerprints:
        return topics, []

    fresh: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for topic in topics:
        fingerprint = _topic_fingerprint(topic)
        if fingerprint in fingerprints:
            skipped.append(
                {
                    "id": topic.get("id"),
                    "title_zh": topic.get("title_zh"),
                    "reason": f"story_db:{fingerprint[:80]}",
                }
            )
        else:
            fresh.append(topic)
    if skipped:
        logger.info("Story DB skipped %d repeated topics", len(skipped))
    return fresh, skipped
