from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import feedparser

from collectors.base import PostItem

logger = logging.getLogger(__name__)


def _entry_datetime(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def collect_rss(config: dict, window_hours: int) -> list[PostItem]:
    feeds = config.get("rss_feeds", [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    posts: list[PostItem] = []
    seen_urls: set[str] = set()

    for feed_cfg in feeds:
        name = feed_cfg.get("name", "rss")
        url = feed_cfg.get("url")
        if not url:
            continue

        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", name, exc)
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            logger.warning("RSS parse issue for %s: %s", name, getattr(parsed, "bozo_exception", ""))
            continue

        for entry in parsed.entries:
            link = entry.get("link") or ""
            if not link or link in seen_urls:
                continue

            created = _entry_datetime(entry)
            if created is None:
                continue
            if created < cutoff:
                continue

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            text = f"{title}\n\n{summary}".strip() if summary else title
            if not text:
                continue

            seen_urls.add(link)
            posts.append(
                PostItem(
                    source="rss",
                    text=text[:4000],
                    title=title or None,
                    url=link,
                    created_at=created,
                    feed_name=name,
                )
            )

    logger.info("RSS collector: %d posts", len(posts))
    return posts
