from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from collectors.base import PostItem
from generator.evidence_pack import collect_evidence_source_urls, normalize_source_url
from analyzer.shortlist import NON_ARTICLE_HOSTS


def _is_article_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    if not host:
        return False
    return not any(host == item or host.endswith(f".{item}") for item in NON_ARTICLE_HOSTS)


def _used_urls(topics: list[dict[str, Any]]) -> set[str]:
    return collect_evidence_source_urls(topics)


def build_fallback_article_topics(
    posts: list[PostItem],
    existing_topics: list[dict[str, Any]],
    needed: int,
) -> list[dict[str, Any]]:
    if needed <= 0:
        return []

    used = _used_urls(existing_topics)
    fallback: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for post in posts:
        canonical_url = normalize_source_url(post.url)
        if not canonical_url or canonical_url in used or canonical_url in seen_urls:
            continue
        if not _is_article_url(post.url):
            continue

        title = post.title or post.text[:80]
        fallback.append(
            {
                "id": f"fallback_{len(fallback) + 1:02d}",
                "title_zh": title,
                "summary": post.text[:240],
                "heat_score": int(min(max(float(post.extra.get("shortlist_score", post.raw_score) or 0), 50), 74)),
                "evidence_urls": [post.url],
                "publish_recommendation": "publish",
                "skip_reason": None,
                "fallback_reason": "article_backed_shortlist_fill",
            }
        )
        seen_urls.add(canonical_url)
        if len(fallback) >= needed:
            break
    return fallback
