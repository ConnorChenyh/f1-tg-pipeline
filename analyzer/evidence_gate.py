from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SOCIAL_SOURCES = {"reddit", "twitter", "x"}


def _gate_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("evidence_gate", {}) or {}


def evidence_gate_enabled(config: dict[str, Any]) -> bool:
    return bool(_gate_config(config).get("enabled", True))


def _has_article_content(topic: dict[str, Any]) -> bool:
    return any(
        (post.get("article_content") or "").strip() and post.get("fetch_status") == "ok"
        for post in topic.get("evidence_posts", []) or []
        if isinstance(post, dict)
    )


def _source_set(topic: dict[str, Any]) -> set[str]:
    return {
        str(post.get("source") or "").lower()
        for post in topic.get("evidence_posts", []) or []
        if isinstance(post, dict)
    }


def _social_only(topic: dict[str, Any]) -> bool:
    sources = _source_set(topic)
    return bool(sources) and sources <= SOCIAL_SOURCES


def filter_topics_by_evidence_quality(
    topics: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not evidence_gate_enabled(config):
        return topics, []

    cfg = _gate_config(config)
    allow_social_only_heat = int(cfg.get("allow_social_only_heat", 88))
    min_article_backed_topics = int(cfg.get("min_article_backed_topics", 2))

    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    article_backed_count = sum(1 for topic in topics if _has_article_content(topic))

    for topic in topics:
        heat = int(topic.get("heat_score", 0) or 0)
        has_article = _has_article_content(topic)
        social_only = _social_only(topic)

        if social_only and not has_article and heat < allow_social_only_heat and article_backed_count >= min_article_backed_topics:
            skipped.append(
                {
                    "id": topic.get("id"),
                    "title_zh": topic.get("title_zh"),
                    "reason": "social_only_without_article_content",
                }
            )
            continue

        item = dict(topic)
        notes = list(item.get("evidence_quality_notes", []) or [])
        if social_only and not has_article:
            notes.append("social-only evidence; write as rumor/discussion unless corroborated")
        elif has_article:
            notes.append("article-backed evidence available")
        item["evidence_quality_notes"] = notes
        kept.append(item)

    if skipped:
        logger.info("Evidence gate skipped %d low-evidence topics", len(skipped))
    return kept, skipped
