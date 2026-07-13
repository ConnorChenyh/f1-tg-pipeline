from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from collectors.base import PostItem


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc.lower()}{path}"


def find_evidence_posts(topic: dict[str, Any], posts: list[PostItem]) -> list[dict[str, Any]]:
    evidence_urls = topic.get("evidence_urls") or []
    if not evidence_urls:
        return []

    by_url = {_normalize_url(post.url): post for post in posts if post.url}
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in evidence_urls:
        key = _normalize_url(url)
        post = by_url.get(key)
        if post is None:
            for post_key, candidate in by_url.items():
                if key in post_key or post_key in key:
                    post = candidate
                    break
        if post is None:
            matched.append(
                {
                    "url": url,
                    "source": "unknown",
                    "title": None,
                    "text": "",
                    "created_at": None,
                    "note": "URL listed in topic but not found in collected posts",
                }
            )
            continue

        norm = _normalize_url(post.url)
        if norm in seen:
            continue
        seen.add(norm)

        text = post.text
        if len(text) > 1200:
            text = text[:1200] + "..."

        matched.append(
            {
                "url": post.url,
                "source": post.source,
                "title": post.title,
                "text": text,
                "created_at": post.created_at.isoformat(),
                "likes": post.likes,
                "replies": post.replies,
            }
        )

    return matched


def enrich_topics_with_evidence(
    topics: list[dict[str, Any]],
    posts: list[PostItem],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for topic in topics:
        item = dict(topic)
        item["evidence_posts"] = find_evidence_posts(topic, posts)
        enriched.append(item)
    return enriched
