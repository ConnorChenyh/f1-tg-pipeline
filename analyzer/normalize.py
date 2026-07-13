from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Iterable

from collectors.base import PostItem

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = HTML_TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _title_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def filter_by_window(posts: Iterable[PostItem], window_hours: int) -> list[PostItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    filtered: list[PostItem] = []
    for post in posts:
        created = post.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            filtered.append(post)
    return filtered


def dedupe_posts(posts: list[PostItem]) -> list[PostItem]:
    by_url: dict[str, PostItem] = {}
    for post in posts:
        key = post.url.strip().lower()
        if not key:
            continue
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = post
            continue
        if (post.likes + post.replies + post.retweets) > (
            existing.likes + existing.replies + existing.retweets
        ):
            by_url[key] = post

    unique = list(by_url.values())

    # Cross-source title dedupe for near-duplicates.
    kept: list[PostItem] = []
    for post in sorted(unique, key=lambda p: p.created_at, reverse=True):
        duplicate = False
        for other in kept:
            if _title_similarity(post.title, other.title) >= 0.85:
                duplicate = True
                break
        if not duplicate:
            kept.append(post)

    logger.info("Normalize: %d -> %d after dedupe", len(posts), len(kept))
    return kept


def normalize_posts(posts: list[PostItem], window_hours: int) -> list[PostItem]:
    cleaned: list[PostItem] = []
    for post in posts:
        cleaned.append(
            PostItem(
                source=post.source,
                text=clean_text(post.text),
                title=clean_text(post.title) if post.title else None,
                url=post.url,
                created_at=post.created_at,
                likes=post.likes,
                replies=post.replies,
                retweets=post.retweets,
                raw_score=post.raw_score,
                feed_name=post.feed_name,
                extra=post.extra,
            )
        )

    in_window = filter_by_window(cleaned, window_hours)
    return dedupe_posts(in_window)
