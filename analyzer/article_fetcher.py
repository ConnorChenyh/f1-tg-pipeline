from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from analyzer.net import RetryExhaustedError, RetryPolicy, request_with_retry
from analyzer.url_safety import UnsafeUrlError, assert_fetchable_url, resolve_and_validate

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


MAX_REDIRECTS = 5


class _PinnedAddressAdapter(requests.adapters.HTTPAdapter):
    """Connect to an already-validated address, not to a fresh DNS answer.

    Hostname verification and SNI stay on the original name, so TLS remains
    correct; only the address actually dialled is pinned.
    """

    def __init__(self, host: str, address: str, **kwargs: Any) -> None:
        self._pinned_host = host
        self._pinned_address = address
        super().__init__(**kwargs)

    def _resolve(self, host: str, port: int) -> str:
        if host == self._pinned_host:
            return self._pinned_address
        return host

    @staticmethod
    def _connection_kwargs(
        host: str,
        scheme: str | None,
        pool_kwargs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Keep TLS hostname settings off plain HTTP connection pools."""
        result = dict(pool_kwargs or {})
        if (scheme or "").lower() == "https":
            result.setdefault("assert_hostname", host)
            result.setdefault("server_hostname", host)
        return result

    def send(self, request, **kwargs):  # type: ignore[override]
        original = self.poolmanager.connection_from_host

        # The pool connects to an IP address, so urllib3 would otherwise emit
        # that address as Host. Preserve the hostname (and any explicit port)
        # used by the article URL for virtual hosting.
        parsed = urlparse(request.url)
        hostname = parsed.hostname or self._pinned_host
        default_port = 443 if parsed.scheme == "https" else 80
        host_header = hostname
        if parsed.port is not None and parsed.port != default_port:
            host_header = f"{hostname}:{parsed.port}"
        request.headers["Host"] = host_header

        def pinned(host, port=None, scheme=None, pool_kwargs=None):
            kwargs2 = self._connection_kwargs(host, scheme, pool_kwargs)
            return original(self._resolve(host, port or 0), port, scheme, kwargs2)

        self.poolmanager.connection_from_host = pinned  # type: ignore[assignment]
        try:
            return super().send(request, **kwargs)
        finally:
            self.poolmanager.connection_from_host = original  # type: ignore[assignment]


def _fetch_following_safe_redirects(
    url: str,
    timeout_sec: int,
    headers: dict[str, str],
    policy: RetryPolicy | None,
    method: str,
) -> requests.Response:
    """GET a URL, validating every redirect hop before following it.

    Automatic redirect following would let a redirect land on the host's own
    network or a metadata endpoint after the first URL passed validation.
    """
    current = assert_fetchable_url(url)
    for _hop in range(MAX_REDIRECTS + 1):
        def _get(target: str = current) -> requests.Response:
            # Re-resolve per attempt and connect to the checked address, so a
            # changed DNS answer cannot redirect the connection inward.
            checked_url, addresses = resolve_and_validate(target)
            session = requests.Session()
            # Environment proxies would bypass the direct pool patched by the
            # pinned adapter and let the proxy resolve the untrusted hostname.
            session.trust_env = False
            parsed = urlparse(checked_url)
            adapter = _PinnedAddressAdapter(
                (parsed.hostname or "").lower(), addresses[0], pool_maxsize=1
            )
            session.mount(f"{parsed.scheme}://", adapter)
            try:
                return session.get(
                    checked_url, timeout=timeout_sec, headers=headers, allow_redirects=False
                )
            finally:
                session.close()

        response = request_with_retry(_get, method=method, policy=policy)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location")
        if not location:
            return response
        current = assert_fetchable_url(urljoin(current, location))

    raise UnsafeUrlError(f"too many redirects for {url}")


def _fetch_via_jina(
    url: str,
    timeout_sec: int,
    policy: RetryPolicy | None = None,
) -> str | None:
    reader_url = f"https://r.jina.ai/{url}"
    try:
        # The fetch is performed by Jina, not by us, so the target host is not
        # resolved locally; the scheme and literal host are still checked.
        assert_fetchable_url(url, resolve_dns=False)
        response = request_with_retry(
            lambda: requests.get(
                reader_url,
                timeout=timeout_sec,
                headers={"User-Agent": "f1-xhs-pipeline/1.0"},
            ),
            method="jina",
            policy=policy,
        )
        response.raise_for_status()
        text = _extract_jina_article_body(response.text)
        text = _clean_text(text)
        return text if len(text) > 120 else None
    except Exception as exc:
        logger.info("Jina fetch failed for %s: %s", url, exc)
        return None


def _fetch_via_html(
    url: str,
    timeout_sec: int,
    policy: RetryPolicy | None = None,
) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        response = _fetch_following_safe_redirects(url, timeout_sec, headers, policy, "article")
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

    try:
        assert_fetchable_url(url)
    except UnsafeUrlError as exc:
        logger.warning("Refusing to fetch unsafe URL %r: %s", url, exc)
        return {"fetch_status": "unsafe", "article_content": ""}

    timeout_sec = int(fetch_cfg.get("timeout_sec", 15))
    max_chars = int(fetch_cfg.get("max_chars", 3500))
    use_jina = bool(fetch_cfg.get("use_jina", True))
    policy = RetryPolicy.from_config(fetch_cfg.get("retry"))

    content = None
    fetch_method = None

    if use_jina:
        content = _fetch_via_jina(url, timeout_sec, policy)
        fetch_method = "jina"

    if not content:
        content = _fetch_via_html(url, timeout_sec, policy)
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
