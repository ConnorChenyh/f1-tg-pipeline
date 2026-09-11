from __future__ import annotations

import json
import logging
import re
from typing import Any

from analyzer.context import RunContext
from collectors.base import PostItem
from generator.deepseek_client import DeepSeekClient, ResponseShapeError
from generator.prompts import TOPICS_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

TOPICS_USER_TEMPLATE = """Analyze these F1 posts and cluster them into trending topics.

Run context:
{run_context}

Posts JSON (includes created_at for each post):
{posts_json}

Return JSON with this exact shape:
{{
  "topics": [
    {{
      "id": "topic_01",
      "title_zh": "中文话题标题",
      "summary": "一句话中文摘要",
      "heat_score": 0,
      "evidence_urls": ["url1", "url2"],
      "publish_recommendation": "publish",
      "skip_reason": null
    }}
  ]
}}

Rules:
- Return {min_topics} to {max_topics} candidate topics when the input contains enough distinct stories
- heat_score must be an integer between 0 and 100, with no units or extra text
- publish_recommendation must be "publish" or "skip"
- skip rumors, pure flame wars, or posts without verifiable facts
- evidence_urls must come from the input posts
"""


def _coerce_int(value: Any, default: int = 0) -> int:
    """Coerce a model-provided score to int, tolerating '85分' or '85.0'."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group(0))
    return default


def _coerce_heat_score(value: Any) -> int:
    return max(0, min(100, _coerce_int(value, 0)))


def _coerce_heat_score_logged(value: Any, title: str) -> int:
    """Coerce a heat score and record when a value could not be read.

    A score coerced to 0 is usually dropped by heat_threshold, so without this
    line the topic would vanish with no explanation. Note that a coerced topic
    can still be re-added by the minimum-topic backfill below.
    """
    score = _coerce_heat_score(value)
    if score == 0 and value not in (0, "0", None):
        logger.warning(
            "Topic %r had an unreadable heat_score %r; coerced to 0 (may be filtered by heat_threshold)",
            title,
            value,
        )
    return score


def _validate_topics_payload(payload: Any) -> list[dict[str, Any]]:
    """Shape check for the topic extractor; raises ResponseShapeError to re-prompt."""
    if not isinstance(payload, dict):
        raise ResponseShapeError(f"expected a JSON object, got {type(payload).__name__}")
    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list):
        raise ResponseShapeError("'topics' must be a JSON array")
    if not raw_topics:
        raise ResponseShapeError("'topics' must contain at least one candidate")

    cleaned: list[dict[str, Any]] = []
    for index, topic in enumerate(raw_topics, start=1):
        if not isinstance(topic, dict):
            raise ResponseShapeError(f"topics[{index}] must be an object")
        title = str(topic.get("title_zh") or "").strip()
        if not title:
            raise ResponseShapeError(f"topics[{index}].title_zh is required and must be non-empty")
        item = dict(topic)
        item["title_zh"] = title
        item.setdefault("id", f"topic_{index:02d}")
        item["heat_score"] = _coerce_heat_score_logged(topic.get("heat_score"), title)
        urls = topic.get("evidence_urls")
        item["evidence_urls"] = [str(url) for url in urls if url] if isinstance(urls, list) else []
        cleaned.append(item)
    return cleaned


def _compact_posts(posts: list[PostItem], limit: int = 80) -> list[dict[str, Any]]:
    compact = []
    for post in posts[:limit]:
        text = post.text
        if len(text) > 200:
            text = text[:200] + "..."
        compact.append(
            {
                "source": post.source,
                "title": post.title,
                "text": text,
                "url": post.url,
                "created_at": post.created_at.isoformat(),
                "score": post.raw_score,
                "likes": post.likes,
                "replies": post.replies,
                "retweets": post.retweets,
            }
        )
    return compact


def extract_topics(
    client: DeepSeekClient,
    posts: list[PostItem],
    heat_threshold: int,
    run_context: RunContext,
    min_topics: int = 3,
    max_topics: int = 6,
) -> list[dict[str, Any]]:
    if not posts:
        return []

    payload = _compact_posts(posts)
    user_prompt = TOPICS_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        posts_json=json.dumps(payload, ensure_ascii=False),
        min_topics=min_topics,
        max_topics=max_topics,
    )
    candidates = client.chat_json(
        client.model_topics,
        TOPICS_SYSTEM_PROMPT,
        user_prompt,
        validator=_validate_topics_payload,
    )

    candidates = [
        topic
        for topic in candidates
        if str(topic.get("publish_recommendation") or "").lower() != "skip"
    ]

    candidates.sort(key=lambda t: t.get("heat_score") or 0, reverse=True)

    filtered: list[dict[str, Any]] = []
    for topic in candidates:
        heat = topic.get("heat_score") or 0
        if heat < heat_threshold:
            continue
        filtered.append(topic)

    if len(filtered) < min_topics:
        seen_ids = {id(topic) for topic in filtered}
        for topic in candidates:
            if id(topic) in seen_ids:
                continue
            filtered.append(topic)
            if len(filtered) >= min_topics:
                break

    filtered = filtered[:max_topics]
    logger.info("Topics extracted: %d (after filter)", len(filtered))
    return filtered
