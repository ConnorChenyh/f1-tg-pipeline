from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
}

WHITESPACE_RE = re.compile(r"\s+")


def normalize_source_url(url: str) -> str:
    """Return a stable URL for source comparison and prompt grounding."""
    raw = (url or "").strip()
    if not raw:
        return ""

    parsed = urlsplit(raw)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = parsed.path.rstrip("/") or parsed.path
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(query, doseq=True),
            "",
        )
    )


def compact_text(text: str, max_chars: int = 1800) -> str:
    cleaned = WHITESPACE_RE.sub(" ", (text or "").strip())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _source_note(post: dict[str, Any]) -> str:
    source = (post.get("source") or "").lower()
    fetch_status = post.get("fetch_status")
    article_content = (post.get("article_content") or "").strip()

    if article_content and fetch_status == "ok":
        return "article_content is available; use it as the primary source."
    if source in {"reddit", "twitter", "x"}:
        return "social post only; phrase claims as what the post/video says unless corroborated."
    if source == "rss":
        return "RSS/title snippet only; avoid details not explicitly present."
    return "limited source text; avoid unsupported detail."


def build_topic_grounding(topic: dict[str, Any], max_chars: int = 3500) -> dict[str, Any]:
    evidence_items: list[dict[str, Any]] = []
    topic_id = topic.get("id") or "topic"

    for index, post in enumerate(topic.get("evidence_posts", []) or [], start=1):
        article_content = (post.get("article_content") or "").strip()
        post_text = (post.get("text") or "").strip()
        content_basis = "article_content" if article_content and post.get("fetch_status") == "ok" else "post_text"
        content = article_content if content_basis == "article_content" else post_text
        url = post.get("url") or ""

        evidence_items.append(
            {
                "id": f"{topic_id}_e{index}",
                "source": post.get("source"),
                "title": post.get("title"),
                "url": url,
                "canonical_url": normalize_source_url(url),
                "created_at": post.get("created_at"),
                "fetch_status": post.get("fetch_status"),
                "fetch_method": post.get("fetch_method"),
                "content_basis": content_basis,
                "source_note": _source_note(post),
                "content": compact_text(content, max_chars=max_chars),
            }
        )

    return {
        "id": topic.get("id"),
        "title_zh": topic.get("title_zh"),
        "model_summary": topic.get("summary"),
        "heat_score": topic.get("heat_score"),
        "publish_recommendation": topic.get("publish_recommendation"),
        "skip_reason": topic.get("skip_reason"),
        "evidence": evidence_items,
        "writing_constraints": [
            "Treat model_summary as secondary context; evidence content is the source of truth.",
            "Do not merge separate actions into one physical action.",
            "Avoid loaded Chinese idioms that add certainty or drama not present in evidence.",
            "Translate championship, champion, title, race win, and podium according to the evidence wording.",
        ],
    }


def build_digest_grounding(topics: list[dict[str, Any]], max_chars: int = 3500) -> list[dict[str, Any]]:
    return [build_topic_grounding(topic, max_chars=max_chars) for topic in topics]


def collect_evidence_source_urls(topics: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for topic in topics:
        for url in topic.get("evidence_urls", []) or []:
            normalized = normalize_source_url(str(url))
            if normalized:
                urls.add(normalized)
        for post in topic.get("evidence_posts", []) or []:
            normalized = normalize_source_url(str(post.get("url") or ""))
            if normalized:
                urls.add(normalized)
    return urls
