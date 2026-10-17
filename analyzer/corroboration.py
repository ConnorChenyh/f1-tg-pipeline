"""Find article coverage for thinly sourced topics with DeepSeek web search.

A topic backed only by a social post, a video page or an untiered site is
searched once; the best recent results are fetched with the normal article
fetcher and added as evidence. Search snippets are never evidence on their
own: a result only counts once its article body has been fetched. Failures
leave the topic unchanged so the evidence gate decides as before.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from analyzer.article_fetcher import fetch_article_content
from analyzer.net import RetryPolicy, request_with_retry
from analyzer.shortlist import is_article_host, url_source_tier
from generator.evidence_pack import normalize_source_url

logger = logging.getLogger(__name__)

STRONG_TIERS = {"official", "tier1_media", "specialist_media"}
QUERY_MAX_CHARS = 128
QUERY_NOISE_RE = re.compile(r"[\[\]\(\)\"“”|]+")


def _cfg(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("corroboration", {}) or {}


def _min_article_chars(config: dict[str, Any]) -> int:
    return max(1, int((config.get("evidence_gate", {}) or {}).get("min_article_chars", 0)))


def _has_strong_article(topic: dict[str, Any], config: dict[str, Any]) -> bool:
    min_chars = _min_article_chars(config)
    return any(
        post.get("fetch_status") == "ok"
        and len((post.get("article_content") or "").strip()) >= min_chars
        and url_source_tier(str(post.get("url") or ""), config) in STRONG_TIERS
        for post in topic.get("evidence_posts", []) or []
        if isinstance(post, dict)
    )


def build_query(topic: dict[str, Any], now: datetime, lookback_days: float) -> str | None:
    """English-results query from the first English evidence title, limited by date."""
    title = ""
    for post in topic.get("evidence_posts", []) or []:
        candidate = QUERY_NOISE_RE.sub(" ", str(post.get("title") or "")).strip()
        if candidate and candidate.isascii():
            title = " ".join(candidate.split())
            break
    if not title:
        return None
    suffix = f" lang:en after:{(now - timedelta(days=lookback_days)).date().isoformat()}"
    return title[: QUERY_MAX_CHARS - len(suffix)].rstrip() + suffix


def search_web(query: str, config: dict[str, Any], count: int) -> list[dict[str, Any]]:
    deepseek_cfg = config.get("deepseek", {}) or {}
    api_key = os.getenv(deepseek_cfg.get("api_key_env", "DEEPSEEK_API_KEY"))
    if not api_key:
        raise RuntimeError("DeepSeek API key is not set")
    base_url = str(deepseek_cfg.get("base_url", "https://api.deepseek.com/v1")).rstrip("/")
    timeout_sec = float(_cfg(config).get("timeout_sec", 15))
    response = request_with_retry(
        lambda: requests.post(
            f"{base_url}/tools/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "count": count},
            timeout=timeout_sec,
        ),
        method="search",
        policy=RetryPolicy(attempts=2),
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    return [item for item in results if isinstance(item, dict)]


def _parse_date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def select_results(
    results: list[dict[str, Any]],
    config: dict[str, Any],
    now: datetime,
    exclude_urls: set[str],
) -> list[dict[str, Any]]:
    """Relevant recent article results, tiered sites first."""
    cfg = _cfg(config)
    min_authority = int(cfg.get("min_authority", 3))
    min_score = float(cfg.get("min_rerank_score", 0.8))
    cutoff = now - timedelta(days=float(cfg.get("lookback_days", 3)) + 1)
    seen_hosts: set[str] = set()
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for item in results:
        url = str(item.get("url") or "")
        normalized = normalize_source_url(url)
        if not url.startswith(("http://", "https://")) or normalized in exclude_urls or not is_article_host(url):
            continue
        published = _parse_date(item.get("date"))
        # Results dated after the run cannot have informed it (matters for replays).
        if published is not None and not cutoff <= published <= now + timedelta(days=1):
            continue
        score = float(item.get("rerank_score") or 0.0)
        if score < min_score:
            continue
        strong = url_source_tier(url, config) in STRONG_TIERS
        authority = int(item.get("authority") or 0)
        if not strong and authority < min_authority:
            continue
        ranked.append((1 if strong else 0, score, item))
    ranked.sort(key=lambda row: row[:2], reverse=True)
    selected = []
    for *_, item in ranked:
        host = urlparse(str(item["url"])).netloc.lower().removeprefix("www.")
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        selected.append(item)
    return selected


def corroborate_topics(
    topics: list[dict[str, Any]],
    config: dict[str, Any],
    now: datetime,
    exclude_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    cfg = _cfg(config)
    if not cfg.get("enabled", False):
        return topics
    max_topics = int(cfg.get("max_topics", 4))
    max_articles = int(cfg.get("max_articles_per_topic", 2))
    lookback_days = float(cfg.get("lookback_days", 3))
    min_chars = _min_article_chars(config)
    excluded = set(exclude_urls or set())

    searched = 0
    result: list[dict[str, Any]] = []
    for topic in topics:
        if searched >= max_topics or _has_strong_article(topic, config):
            result.append(topic)
            continue
        query = build_query(topic, now, lookback_days)
        if not query:
            result.append(topic)
            continue
        searched += 1
        item = dict(topic)
        record: dict[str, Any] = {"query": query, "added": []}
        try:
            results = search_web(query, config, count=int(cfg.get("result_count", 10)))
        except Exception as exc:
            logger.warning("Corroboration search failed for %r: %s", topic.get("title_zh"), exc)
            record["status"] = f"search_failed:{type(exc).__name__}"
            item["corroboration"] = record
            result.append(item)
            continue

        known = excluded | {
            normalize_source_url(str(post.get("url") or ""))
            for post in item.get("evidence_posts", []) or [] if isinstance(post, dict)
        }
        evidence_posts = list(item.get("evidence_posts", []) or [])
        evidence_urls = list(item.get("evidence_urls", []) or [])
        for found in select_results(results, config, now, known):
            if len(record["added"]) >= max_articles:
                break
            fetched = fetch_article_content(str(found["url"]), config)
            if fetched.get("fetch_status") != "ok" or len(fetched.get("article_content") or "") < min_chars:
                continue
            evidence_posts.append({
                "url": found["url"],
                "source": "search",
                "title": found.get("title"),
                "text": str(found.get("snippet") or "")[:1200],
                "created_at": found.get("date"),
                "site_name": found.get("site_name"),
                **fetched,
            })
            evidence_urls.append(found["url"])
            record["added"].append(found["url"])
        record["status"] = "added" if record["added"] else "no_article_found"
        item["evidence_posts"] = evidence_posts
        item["evidence_urls"] = evidence_urls
        item["corroboration"] = record
        logger.info("Corroboration for %r: %s (%d added)", topic.get("title_zh"), record["status"],
                    len(record["added"]))
        result.append(item)
    return result
