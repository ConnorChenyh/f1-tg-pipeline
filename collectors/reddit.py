from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import yaml

from collectors.base import PostItem

logger = logging.getLogger(__name__)


def _find_rdt_command() -> str | None:
    local_rdt = Path(sys.executable).parent / "rdt"
    if local_rdt.exists():
        return str(local_rdt)
    return shutil.which("rdt")


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


def _extract_rdt_items(data: object) -> list[dict]:
    """Unwrap rdt-cli YAML into flat post dicts."""
    if isinstance(data, list):
        items: list[dict] = []
        for entry in data:
            items.extend(_extract_rdt_items(entry))
        return items

    if not isinstance(data, dict):
        return []

    # rdt-cli listing wrapper: { ok, data: { data: { children: [...] } } }
    nested = data.get("data")
    if isinstance(nested, dict):
        listing = nested.get("data")
        if isinstance(listing, dict) and "children" in listing:
            children = listing.get("children") or []
            posts: list[dict] = []
            for child in children:
                if isinstance(child, dict):
                    post_data = child.get("data")
                    if isinstance(post_data, dict):
                        posts.append(post_data)
            if posts:
                return posts

    # Single post or already-flat dict
    if "title" in data or "selftext" in data:
        return [data]

    return []


def _run_rdt(args: list[str]) -> list[dict]:
    rdt_cmd = _find_rdt_command()
    if not rdt_cmd:
        logger.warning("rdt-cli not found; skip Reddit collector (pipx install rdt-cli)")
        return []

    cmd = [rdt_cmd, *args, "--yaml"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("rdt-cli timed out: %s", " ".join(cmd))
        return []

    if result.returncode != 0:
        logger.warning("rdt-cli failed (%s): %s", result.returncode, result.stderr.strip())
        return []

    output = result.stdout.strip()
    if not output:
        return []

    try:
        data = yaml.safe_load(output)
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse rdt YAML: %s", exc)
        return []

    return _extract_rdt_items(data)


def _post_from_rdt(item: dict, source_label: str) -> PostItem | None:
    title = item.get("title") or ""
    body = item.get("selftext") or item.get("body") or ""
    text = body.strip() or title.strip()
    if not text:
        return None

    url = item.get("url") or item.get("permalink") or ""
    if url and not url.startswith("http"):
        url = f"https://www.reddit.com{url}"

    created_raw = item.get("created_utc") or item.get("created")
    created = None
    if isinstance(created_raw, (int, float)):
        created = datetime.fromtimestamp(created_raw, tz=timezone.utc)
    else:
        created = _parse_datetime(created_raw)
    if created is None:
        created = datetime.now(timezone.utc)

    return PostItem(
        source="reddit",
        text=text[:4000],
        title=title or None,
        url=url,
        created_at=created,
        likes=int(item.get("score") or item.get("ups") or 0),
        replies=int(item.get("num_comments") or item.get("comments") or 0),
        extra={"source_label": source_label, "subreddit": item.get("subreddit")},
    )


def _entry_datetime(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def _collect_subreddit_rss(subreddit: str, limit: int) -> list[PostItem]:
    url = f"https://www.reddit.com/r/{subreddit}/.rss"
    try:
        parsed = feedparser.parse(
            url,
            request_headers={"User-Agent": "Mozilla/5.0 f1-tg-pipeline/1.0"},
        )
    except Exception as exc:
        logger.warning("Reddit RSS fallback failed for r/%s: %s", subreddit, exc)
        return []

    if getattr(parsed, "bozo", False) and not parsed.entries:
        logger.warning("Reddit RSS parse issue for r/%s: %s", subreddit, getattr(parsed, "bozo_exception", ""))
        return []

    posts: list[PostItem] = []
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        summary = (entry.get("summary") or "").strip()
        link = entry.get("link") or ""
        if not title or not link:
            continue

        created = _entry_datetime(entry)
        if created is None:
            created = datetime.now(timezone.utc)

        text = f"{title}\n\n{summary}".strip() if summary else title
        posts.append(
            PostItem(
                source="reddit",
                text=text[:4000],
                title=title,
                url=link,
                created_at=created,
                extra={"source_label": f"rss:{subreddit}", "subreddit": subreddit},
            )
        )

    logger.info("Reddit RSS fallback: %d posts", len(posts))
    return posts


def collect_reddit(config: dict) -> list[PostItem]:
    reddit_cfg = config.get("reddit", {})
    subreddit = reddit_cfg.get("subreddit", "formula1")
    sub_limit = int(reddit_cfg.get("sub_limit", 50))
    search_limit = int(reddit_cfg.get("search_limit", 30))
    keywords = reddit_cfg.get("search_keywords", ["F1"])

    posts: list[PostItem] = []
    seen_urls: set[str] = set()

    sub_items = _run_rdt(["sub", subreddit, "--limit", str(sub_limit)])
    for item in sub_items:
        post = _post_from_rdt(item, f"sub:{subreddit}")
        if post and post.url not in seen_urls:
            seen_urls.add(post.url)
            posts.append(post)

    if not posts:
        for post in _collect_subreddit_rss(subreddit, sub_limit):
            if post.url not in seen_urls:
                seen_urls.add(post.url)
                posts.append(post)

    for keyword in keywords:
        search_items = _run_rdt(["search", keyword, "--limit", str(search_limit)])
        for item in search_items:
            post = _post_from_rdt(item, f"search:{keyword}")
            if post and post.url not in seen_urls:
                seen_urls.add(post.url)
                posts.append(post)

    logger.info("Reddit collector: %d posts", len(posts))
    return posts
