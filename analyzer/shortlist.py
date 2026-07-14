from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from collectors.base import PostItem
from generator.evidence_pack import normalize_source_url

logger = logging.getLogger(__name__)

TEXT_RE = re.compile(r"\s+")

DEFAULT_SOURCE_TIERS = {
    "official": {"weight": 1.8, "domains": ["formula1.com", "fia.com"]},
    "tier1_media": {
        "weight": 1.45,
        "domains": ["autosport.com", "motorsport.com", "espn.com", "skysports.com", "bbc.com", "the-race.com"],
    },
    "specialist_media": {
        "weight": 1.25,
        "domains": ["racingnews365.com", "planetf1.com", "formu1a.uno", "auto-motor-und-sport.de"],
    },
    "social": {"weight": 0.72, "sources": ["reddit", "twitter"]},
    "rss": {"weight": 1.0, "sources": ["rss"]},
}

NON_ARTICLE_HOSTS = {
    "i.redd.it",
    "v.redd.it",
    "reddit.com",
    "x.com",
    "twitter.com",
    "youtu.be",
    "youtube.com",
    "imgur.com",
    "dubz.link",
    "streamable.com",
}


def _shortlist_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("shortlist", {}) or {}


def shortlist_enabled(config: dict[str, Any]) -> bool:
    return bool(_shortlist_config(config).get("enabled", True))


def _source_tiers(config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("source_tiers", {}) or {}
    if not configured:
        return DEFAULT_SOURCE_TIERS
    merged = {key: dict(value) for key, value in DEFAULT_SOURCE_TIERS.items()}
    for key, value in configured.items():
        if isinstance(value, dict):
            merged[key] = value
    return merged


def _host(url: str) -> str:
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _source_tier(post: PostItem, config: dict[str, Any]) -> tuple[str, float]:
    host = _host(post.url)
    source = (post.source or "").lower()
    for tier_name, tier in _source_tiers(config).items():
        domains = [str(item).lower() for item in tier.get("domains", []) or []]
        sources = [str(item).lower() for item in tier.get("sources", []) or []]
        if host and any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return tier_name, float(tier.get("weight", 1.0))
        if source and source in sources:
            return tier_name, float(tier.get("weight", 1.0))
    return "unknown", 1.0


def _title_text(post: PostItem) -> str:
    raw = f"{post.title or ''} {post.text or ''}"
    return TEXT_RE.sub(" ", raw.lower()).strip()


def _similarity(a: PostItem, b: PostItem) -> float:
    left = _title_text(a)
    right = _title_text(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left[:240], right[:240]).ratio()


def _age_hours(post: PostItem, now: datetime) -> float:
    created = post.created_at
    if created.tzinfo is None and now.tzinfo is not None:
        created = created.replace(tzinfo=now.tzinfo)
    try:
        return max((now - created).total_seconds() / 3600, 0.0)
    except TypeError:
        return 0.0


def _time_decay(post: PostItem, now: datetime, half_life_hours: float) -> float:
    if half_life_hours <= 0:
        return 1.0
    return math.pow(0.5, _age_hours(post, now) / half_life_hours)


def _has_external_article(post: PostItem) -> bool:
    host = _host(post.url)
    if not host:
        return False
    return not any(host == item or host.endswith(f".{item}") for item in NON_ARTICLE_HOSTS)


def _cross_source_count(post: PostItem, posts: list[PostItem]) -> int:
    count = 0
    for other in posts:
        if other is post or other.source == post.source:
            continue
        if normalize_source_url(other.url) and normalize_source_url(other.url) == normalize_source_url(post.url):
            count += 1
            continue
        if _similarity(post, other) >= 0.72:
            count += 1
    return count


def _shortlist_score(post: PostItem, posts: list[PostItem], config: dict[str, Any], now: datetime) -> tuple[float, list[str]]:
    cfg = _shortlist_config(config)
    half_life = float(cfg.get("time_decay_half_life_hours", 24))
    tier, tier_weight = _source_tier(post, config)
    engagement = math.log1p(max(post.likes + post.replies * 2 + post.retweets * 3, 0))
    external_bonus = float(cfg.get("external_article_bonus", 18)) if _has_external_article(post) else 0.0
    cross_count = _cross_source_count(post, posts)
    cross_bonus = cross_count * float(cfg.get("cross_source_bonus", 12))
    base = float(post.raw_score or 0.0) + engagement * 10.0 + external_bonus + cross_bonus
    score = base * tier_weight * _time_decay(post, now, half_life)
    reasons = [f"tier:{tier}", f"tier_weight:{tier_weight:.2f}"]
    if external_bonus:
        reasons.append("external_article")
    if cross_count:
        reasons.append(f"cross_source:{cross_count}")
    social_only_cap = float(cfg.get("social_only_score_cap", 850))
    if tier == "social" and not external_bonus and social_only_cap > 0 and score > social_only_cap:
        score = social_only_cap
        reasons.append(f"social_only_cap:{social_only_cap:.0f}")
    return score, reasons


def shortlist_posts(posts: list[PostItem], config: dict[str, Any], now: datetime) -> list[PostItem]:
    if not shortlist_enabled(config):
        return posts

    cfg = _shortlist_config(config)
    limit = int(cfg.get("limit", 80))
    min_score = float(cfg.get("min_score", 0))
    similarity_threshold = float(cfg.get("similarity_threshold", 0.84))
    allow_same_url = bool(cfg.get("allow_same_url", False))
    max_social_only_posts = int(cfg.get("max_social_only_posts", 12))

    candidates: list[PostItem] = []
    for post in posts:
        score, reasons = _shortlist_score(post, posts, config, now)
        if score < min_score:
            continue
        extra = dict(post.extra or {})
        extra["shortlist_score"] = score
        extra["shortlist_reasons"] = reasons
        candidates.append(
            PostItem(
                source=post.source,
                text=post.text,
                title=post.title,
                url=post.url,
                created_at=post.created_at,
                likes=post.likes,
                replies=post.replies,
                retweets=post.retweets,
                raw_score=post.raw_score,
                feed_name=post.feed_name,
                extra=extra,
            )
        )

    candidates.sort(key=lambda item: float(item.extra.get("shortlist_score", 0.0)), reverse=True)

    selected: list[PostItem] = []
    selected_urls: set[str] = set()
    social_only_count = 0
    for candidate in candidates:
        normalized_url = normalize_source_url(candidate.url)
        tier, _ = _source_tier(candidate, config)
        is_social_only = tier == "social" and not _has_external_article(candidate)
        if is_social_only and max_social_only_posts >= 0 and social_only_count >= max_social_only_posts:
            continue
        if normalized_url and not allow_same_url and normalized_url in selected_urls:
            continue
        if any(_similarity(candidate, existing) >= similarity_threshold for existing in selected):
            continue
        selected.append(candidate)
        if is_social_only:
            social_only_count += 1
        if normalized_url:
            selected_urls.add(normalized_url)
        if len(selected) >= limit:
            break

    logger.info("Shortlist selected %d/%d posts", len(selected), len(posts))
    return selected
