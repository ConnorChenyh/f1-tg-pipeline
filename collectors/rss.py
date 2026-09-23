from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

from analyzer.file_state import write_json_atomic
from analyzer.net import RetryPolicy, request_with_retry
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
    runtime = config.get("_runtime", {})
    cache_path = Path(runtime["root"]) / "output" / "rss_cache.json" if runtime.get("root") else None
    cached_feeds = {}
    if cache_path:
        try:
            cached_feeds = json.loads(cache_path.read_text())
            if not isinstance(cached_feeds, dict):
                cached_feeds = {}
        except (OSError, ValueError):
            pass
    current_urls = {feed.get("url") for feed in feeds}
    cached_feeds = {url: value for url, value in cached_feeds.items() if url in current_urls}
    seen_urls: set[str] = set()

    for feed_cfg in feeds:
        name = feed_cfg.get("name", "rss")
        url = feed_cfg.get("url")
        if not url:
            continue

        started = time.monotonic()
        status = {"name": name, "url": url, "status": "failed", "item_count": 0}
        config.setdefault("_collection_status", []).append(status)
        try:
            settings = config.get("rss_fetch", {})
            cached = cached_feeds.get(url, {})
            if not isinstance(cached, dict):
                cached = {}
            headers = {"User-Agent": "f1-tg-pipeline/1.0"}
            cached_body = None
            try:
                cached_body = base64.b64decode(cached["body"], validate=True)
                for header_name, key in (("If-None-Match", "etag"), ("If-Modified-Since", "last_modified")):
                    if cached.get(key):
                        headers[header_name] = cached[key]
            except (KeyError, ValueError, TypeError):
                pass
            response = request_with_retry(
                lambda: requests.get(url, headers=headers, timeout=float(settings.get("timeout_sec", 15))),
                method="rss", policy=RetryPolicy.from_config(settings.get("retry")),
            )
            response.raise_for_status()
            body = cached_body if response.status_code == 304 else response.content
            if body is None or len(body) > 2_000_000:
                raise ValueError("Missing or oversized RSS response")
            parsed = feedparser.parse(body)
            status["cached"] = response.status_code == 304
            status["http_status"] = response.status_code
            status["status"] = "ok"
        except Exception as exc:
            status["error"] = type(exc).__name__
            logger.warning("RSS fetch failed for %s: %s", name, type(exc).__name__)
            continue
        finally:
            status["duration_sec"] = round(time.monotonic() - started, 3)

        if getattr(parsed, "bozo", False) and not parsed.entries:
            status.update(status="failed", error="FeedParseError")
            logger.warning("RSS parse issue for %s: %s", name, getattr(parsed, "bozo_exception", ""))
            continue

        cached_feeds[url] = {
            "body": base64.b64encode(body).decode(),
            "etag": response.headers.get("ETag", cached.get("etag")) if response.status_code == 304 else response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified", cached.get("last_modified")) if response.status_code == 304 else response.headers.get("Last-Modified"),
        }
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
            status["item_count"] += 1
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

    if cache_path and runtime.get("persist"):
        try:
            write_json_atomic(cache_path, cached_feeds)
        except OSError as exc:
            logger.warning("Could not save RSS cache: %s", type(exc).__name__)
    logger.info("RSS collector: %d posts", len(posts))
    return posts
