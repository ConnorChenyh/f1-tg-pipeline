from __future__ import annotations

import json
import logging
from typing import Any

from analyzer.context import RunContext
from collectors.base import PostItem
from generator.deepseek_client import DeepSeekClient
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
- heat_score is 0-100
- publish_recommendation must be "publish" or "skip"
- skip rumors, pure flame wars, or posts without verifiable facts
- evidence_urls must come from the input posts
"""


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
) -> list[dict[str, Any]]:
    if not posts:
        return []

    payload = _compact_posts(posts)
    user_prompt = TOPICS_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        posts_json=json.dumps(payload, ensure_ascii=False),
    )
    result = client.chat_json(client.model_topics, TOPICS_SYSTEM_PROMPT, user_prompt)

    topics = result.get("topics", []) if isinstance(result, dict) else result
    if not isinstance(topics, list):
        raise ValueError("topics response is not a list")

    filtered: list[dict[str, Any]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        if topic.get("publish_recommendation") == "skip":
            continue
        heat = int(topic.get("heat_score", 0))
        if heat < heat_threshold:
            continue
        filtered.append(topic)

    filtered.sort(key=lambda t: int(t.get("heat_score", 0)), reverse=True)
    logger.info("Topics extracted: %d (after filter)", len(filtered))
    return filtered
