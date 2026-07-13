from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone

import yaml

from collectors.base import PostItem

logger = logging.getLogger(__name__)


def _twitter_enabled(config: dict) -> bool:
    if not config.get("twitter", {}).get("enabled", True):
        return False
    return bool(os.getenv("TWITTER_AUTH_TOKEN") and os.getenv("TWITTER_CT0"))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _run_twitter(args: list[str]) -> list[dict]:
    if not shutil.which("twitter"):
        logger.info("twitter-cli not found; skip Twitter collector")
        return []

    env = os.environ.copy()
    cmd = ["twitter", *args, "--yaml"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("twitter-cli timed out: %s", " ".join(cmd))
        return []

    if result.returncode != 0:
        logger.warning("twitter-cli failed (%s): %s", result.returncode, result.stderr.strip())
        return []

    output = result.stdout.strip()
    if not output:
        return []

    try:
        data = yaml.safe_load(output)
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse twitter YAML: %s", exc)
        return []

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _post_from_tweet(item: dict, source_label: str) -> PostItem | None:
    text = (item.get("text") or item.get("full_text") or "").strip()
    if not text:
        return None

    tweet_id = item.get("id") or item.get("tweet_id")
    url = item.get("url") or (f"https://x.com/i/web/status/{tweet_id}" if tweet_id else "")
    if not url:
        return None

    created = _parse_datetime(item.get("created_at"))
    if created is None:
        created = datetime.now(timezone.utc)

    return PostItem(
        source="twitter",
        text=text[:4000],
        title=None,
        url=url,
        created_at=created,
        likes=int(item.get("favorite_count") or item.get("likes") or 0),
        replies=int(item.get("reply_count") or item.get("replies") or 0),
        retweets=int(item.get("retweet_count") or item.get("retweets") or 0),
        extra={"source_label": source_label},
    )


def collect_twitter(config: dict) -> list[PostItem]:
    if not _twitter_enabled(config):
        logger.info("Twitter collector skipped (missing credentials or disabled)")
        return []

    twitter_cfg = config.get("twitter", {})
    search_limit = int(twitter_cfg.get("search_limit", 30))
    queries = twitter_cfg.get("search_queries", ["F1"])

    posts: list[PostItem] = []
    seen_urls: set[str] = set()

    for query in queries:
        items = _run_twitter(["search", query, "-n", str(search_limit)])
        for item in items:
            post = _post_from_tweet(item, f"search:{query}")
            if post and post.url not in seen_urls:
                seen_urls.add(post.url)
                posts.append(post)

    logger.info("Twitter collector: %d posts", len(posts))
    return posts
