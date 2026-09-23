from __future__ import annotations

from typing import Any
from generator.evidence_pack import normalize_source_url

from collectors.base import PostItem


def _normalize_url(url: str) -> str:
    return normalize_source_url(url)


def find_evidence_posts(topic: dict[str, Any], posts: list[PostItem]) -> list[dict[str, Any]]:
    evidence_urls = list(topic.get("evidence_urls") or [])
    requested = {_normalize_url(url) for url in evidence_urls}
    for post in posts:
        members = [post.url] + [item["url"] for item in post.extra.get("related_posts", [])]
        if requested.intersection(_normalize_url(url) for url in members):
            evidence_urls.extend(members)
    evidence_urls = list(dict.fromkeys(evidence_urls))
    if not evidence_urls:
        return []

    expanded = []
    for post in posts:
        expanded.append(post)
        expanded.extend(PostItem.from_dict(item) for item in post.extra.get("related_posts", []))
    by_url = {_normalize_url(post.url): post for post in expanded if post.url}
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in evidence_urls:
        key = _normalize_url(url)
        post = by_url.get(key)
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
