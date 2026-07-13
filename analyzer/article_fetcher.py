from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\n{3,}")

SKIP_FETCH_HOSTS = {
    "i.redd.it",
    "v.redd.it",
    "streamable.com",
    "youtube.com",
    "youtu.be",
    "imgur.com",
    "twitter.com",
    "x.com",
}

ARTICLE_BODY_SELECTORS = [
    "article",
    "[role='main']",
    ".article-body",
    ".post-content",
    ".entry-content",
    ".story-body",
    "main",
]


def _clean_text(text: str) -> str:
    text = HTML_TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub("\n\n", text)
    return text.strip()


def _should_fetch(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if host in SKIP_FETCH_HOSTS:
        return False
    if host.endswith("reddit.com"):
        return False
    return True


def _extract_jina_article_body(text: str) -> str:
    marker = "Markdown Content:"
    if marker in text:
        text = text.split(marker, 1)[1]

    paragraphs: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith(("[", "*", "#", "!", "|", "URL Source:", "Published Time:", "Title:")):
            continue
        if block.lower().startswith(("skip to", "formula 1", "home", "news", "schedule")):
            continue
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", block)
        plain = re.sub(r"https?://\S+", "", plain).strip()
        if len(plain) < 80:
            continue
        if plain.count("]") > 3 and len(plain) < 200:
            continue
        paragraphs.append(plain)

    if not paragraphs:
        return _clean_text(text)

    return "\n\n".join(paragraphs[:8])


def _fetch_via_jina(url: str, timeout_sec: int) -> str | None:
    reader_url = f"https://r.jina.ai/{url}"
    try:
        response = requests.get(
            reader_url,
            timeout=timeout_sec,
            headers={"User-Agent": "f1-xhs-pipeline/1.0"},
        )
        response.raise_for_status()
        text = _extract_jina_article_body(response.text)
        text = _clean_text(text)
        return text if len(text) > 120 else None
    except Exception as exc:
        logger.info("Jina fetch failed for %s: %s", url, exc)
        return None


def _fetch_via_html(url: str, timeout_sec: int) -> str | None:
    try:
        response = requests.get(
            url,
            timeout=timeout_sec,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        chunks: list[str] = []
        for selector in ARTICLE_BODY_SELECTORS:
            node = soup.select_one(selector)
            if node:
                chunks.append(_clean_text(node.get_text("\n", strip=True)))
                break

        if not chunks:
            paragraphs = [
                _clean_text(p.get_text(" ", strip=True))
                for p in soup.find_all("p")
                if len(p.get_text(strip=True)) > 40
            ]
            chunks.append("\n\n".join(paragraphs[:12]))

        text = "\n\n".join(chunk for chunk in chunks if chunk).strip()
        return text if len(text) > 120 else None
    except Exception as exc:
        logger.info("HTML fetch failed for %s: %s", url, exc)
        return None


def fetch_article_content(url: str, config: dict[str, Any]) -> dict[str, Any]:
    fetch_cfg = config.get("article_fetch", {})
    if not fetch_cfg.get("enabled", True):
        return {"fetch_status": "disabled", "article_content": ""}

    if not _should_fetch(url):
        return {"fetch_status": "skipped", "article_content": ""}

    timeout_sec = int(fetch_cfg.get("timeout_sec", 15))
    max_chars = int(fetch_cfg.get("max_chars", 3500))
    use_jina = bool(fetch_cfg.get("use_jina", True))

    content = None
    fetch_method = None

    if use_jina:
        content = _fetch_via_jina(url, timeout_sec)
        fetch_method = "jina"

    if not content:
        content = _fetch_via_html(url, timeout_sec)
        fetch_method = "html"

    if not content:
        return {"fetch_status": "failed", "article_content": "", "fetch_method": fetch_method}

    if len(content) > max_chars:
        content = content[:max_chars] + "..."

    return {
        "fetch_status": "ok",
        "article_content": content,
        "fetch_method": fetch_method,
    }


def enrich_evidence_with_articles(
    topics: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    fetch_cfg = config.get("article_fetch", {})
    delay_sec = float(fetch_cfg.get("delay_sec", 1.0))
    cache: dict[str, dict[str, Any]] = {}

    enriched_topics: list[dict[str, Any]] = []
    for topic in topics:
        item = dict(topic)
        evidence_posts: list[dict[str, Any]] = []

        for post in topic.get("evidence_posts", []):
            post_item = dict(post)
            url = post_item.get("url", "")
            if not url:
                evidence_posts.append(post_item)
                continue

            if url in cache:
                post_item.update(cache[url])
                evidence_posts.append(post_item)
                continue

            fetched = fetch_article_content(url, config)
            cache[url] = fetched
            post_item.update(fetched)
            evidence_posts.append(post_item)

            if delay_sec > 0:
                time.sleep(delay_sec)

        item["evidence_posts"] = evidence_posts
        enriched_topics.append(item)

    fetched_count = sum(
        1
        for topic in enriched_topics
        for post in topic.get("evidence_posts", [])
        if post.get("fetch_status") == "ok"
    )
    logger.info("Article fetch: %d URLs loaded with full content", fetched_count)
    return enriched_topics
