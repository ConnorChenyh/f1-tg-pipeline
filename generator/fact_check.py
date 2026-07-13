from __future__ import annotations

import json
import logging
from typing import Any

from analyzer.context import RunContext
from generator.deepseek_client import DeepSeekClient
from generator.prompts import FACT_CHECK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

FACT_CHECK_USER_TEMPLATE = """Review and fix this Xiaohongshu draft.

Run context:
{run_context}

Topic JSON:
{topic_json}

Evidence posts (ONLY trusted facts; each has created_at):
{evidence_json}

Draft JSON:
{draft_json}

Return JSON with this exact shape:
{{
  "title": "...",
  "hook": "...",
  "body": ["..."],
  "hashtags": ["..."],
  "image_briefs": ["..."],
  "sources": ["..."],
  "risk_note": "...",
  "fact_check_notes": ["修正说明1", "修正说明2"]
}}

Rules:
- Remove or rewrite any claim not supported by evidence_posts
- Do not use outdated training knowledge (driver lineups, seat availability, car model years)
- If evidence is speculative, label it as 讨论/传闻/分析, not confirmed fact
- Keep car model years consistent with run context season year unless evidence explicitly names another year
- If uncertain, delete the claim instead of guessing
- fact_check_notes lists major corrections in Chinese; use [] if none
"""


def fact_check_draft(
    client: DeepSeekClient,
    draft: dict[str, Any],
    topic: dict[str, Any],
    run_context: RunContext,
) -> dict[str, Any]:
    evidence = topic.get("evidence_posts", [])
    user_prompt = FACT_CHECK_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        topic_json=json.dumps(topic, ensure_ascii=False),
        evidence_json=json.dumps(evidence, ensure_ascii=False),
        draft_json=json.dumps(draft, ensure_ascii=False),
    )
    checked = client.chat_json(client.model_writer, FACT_CHECK_SYSTEM_PROMPT, user_prompt)
    if not isinstance(checked, dict):
        raise ValueError("fact-check response is not an object")

    notes = checked.get("fact_check_notes", []) or []
    if notes:
        logger.info("Fact-check corrections: %s", "; ".join(str(n) for n in notes))

    checked.pop("fact_check_notes", None)
    return checked, notes


DIGEST_FACT_CHECK_USER_TEMPLATE = """Review and fix this Xiaohongshu digest draft.

Run context:
{run_context}

Digest title (keep exactly):
{digest_title}

Topic metadata:
{topics_json}

Grounded evidence packet (use this as the primary source):
{grounding_json}

Draft JSON:
{draft_json}

Deterministic quality guard issues to fix:
{quality_issues_json}

Return JSON with this exact shape:
{{
  "title": "{digest_title}",
  "hook": "...",
  "items": [
    {{"ordinal": "一", "headline": "...", "content": "..."}}
  ],
  "hashtags": ["..."],
  "sources": ["..."],
  "risk_note": "...",
  "fact_check_notes": ["修正说明1"]
}}

Rules:
- Keep title exactly as digest title
- Keep ordinals 一、二、三 format
- Verify claims against grounded evidence.content; article_content content_basis is strongest
- Treat model summaries as secondary context, not source truth
- Fix causal/temporal errors (e.g. conflating separate events)
- Fix every deterministic quality guard issue before returning JSON
- For social/video-only evidence, write “视频显示/帖文称/讨论称” instead of unanchored fact wording
- Avoid ambiguous translations such as 新科冠军 unless evidence clearly supports the exact meaning
- Check technical terms against evidence wording; if a Chinese translation is not clearly standard, keep the English term or write English plus a short Chinese explanation
- In particular, do not invent Chinese names for acronyms such as STM or named aero/PU systems
- Avoid overdramatic idioms such as 军令状、暗流涌动、最大谜题、亲自下场、满天飞
- Remove unsupported claims; use 讨论/传闻/分析 for speculation
- Do not use outdated training knowledge
- fact_check_notes in Chinese; [] if none
"""


def _compact_topic_metadata(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": topic.get("id"),
            "title_zh": topic.get("title_zh"),
            "summary": topic.get("summary"),
            "heat_score": topic.get("heat_score"),
            "evidence_urls": topic.get("evidence_urls"),
        }
        for topic in topics
    ]


def fact_check_digest(
    client: DeepSeekClient,
    draft: dict[str, Any],
    topics: list[dict[str, Any]],
    run_context: RunContext,
    digest_title: str,
    grounding: list[dict[str, Any]] | None = None,
    quality_issues: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    user_prompt = DIGEST_FACT_CHECK_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        digest_title=digest_title,
        topics_json=json.dumps(_compact_topic_metadata(topics), ensure_ascii=False),
        grounding_json=json.dumps(grounding or topics, ensure_ascii=False),
        draft_json=json.dumps(draft, ensure_ascii=False),
        quality_issues_json=json.dumps(quality_issues or [], ensure_ascii=False),
    )
    checked = client.chat_json(client.model_writer, FACT_CHECK_SYSTEM_PROMPT, user_prompt)
    if not isinstance(checked, dict):
        raise ValueError("digest fact-check response is not an object")

    notes = checked.get("fact_check_notes", []) or []
    if notes:
        logger.info("Digest fact-check corrections: %s", "; ".join(str(n) for n in notes))

    checked.pop("fact_check_notes", None)
    checked["title"] = digest_title
    return checked, notes
