from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from analyzer.context import RunContext
from generator.deepseek_client import DeepSeekClient
from generator.evidence_pack import build_topic_grounding
from generator.fact_check import fact_check_draft
from generator.prompts import WRITER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

WRITER_USER_TEMPLATE = """Create one Xiaohongshu draft for this topic.

Run context:
{run_context}

Topic JSON:
{topic_json}

Grounded evidence packet (primary source of truth; respect created_at):
{grounding_json}

Return JSON with this exact shape:
{{
  "title": "不超过20字",
  "hook": "开头抓人一句",
  "body": ["段落1", "段落2", "段落3"],
  "hashtags": ["#F1", "#标签"],
  "image_briefs": ["封面说明", "图2说明", "图3说明"],
  "sources": ["url1", "url2"],
  "risk_note": "风险说明，没有风险写无"
}}

Rules:
- Total body length around 300-600 Chinese characters
- body has 3-5 short paragraphs
- sources must reuse topic evidence_urls when possible
- image_briefs should be concrete for visual design
- ONLY state facts that appear in grounded evidence content; do not use outdated training knowledge
- For transfer rumors, driver lineups, seat availability, or car model years: if evidence does not say it, do not claim it
- Distinguish 已确认事实 vs 讨论/传闻/分析
- For social/video-only evidence, write “视频显示/帖文称/讨论称” instead of unanchored fact wording
- Avoid ambiguous translations such as 新科冠军 unless evidence clearly supports the exact meaning
- Avoid overdramatic idioms such as 军令状、暗流涌动、最大谜题、亲自下场、满天飞
- Align car/year references with run context season year unless evidence explicitly names another year
- If evidence is thin or speculative, say so in risk_note and keep the tone cautious
"""


def generate_draft(
    client: DeepSeekClient,
    topic: dict[str, Any],
    run_context: RunContext,
    fact_check_enabled: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    grounding = build_topic_grounding(topic)
    user_prompt = WRITER_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        topic_json=json.dumps(topic, ensure_ascii=False),
        grounding_json=json.dumps(grounding, ensure_ascii=False),
    )
    draft = client.chat_json(client.model_writer, WRITER_SYSTEM_PROMPT, user_prompt)
    if not isinstance(draft, dict):
        raise ValueError("draft response is not an object")

    fact_check_notes: list[str] = []
    if fact_check_enabled:
        draft, fact_check_notes = fact_check_draft(client, draft, topic, run_context)

    return draft, fact_check_notes


def draft_to_markdown(draft: dict[str, Any], fact_check_notes: list[str] | None = None) -> str:
    lines = [
        f"# {draft.get('title', '')}",
        "",
        draft.get("hook", ""),
        "",
    ]
    for paragraph in draft.get("body", []):
        lines.append(paragraph)
        lines.append("")

    hashtags = " ".join(draft.get("hashtags", []))
    if hashtags:
        lines.append(hashtags)
        lines.append("")

    sources = draft.get("sources", [])
    if sources:
        lines.append("## Sources")
        for source in sources:
            lines.append(f"- {source}")

    risk = draft.get("risk_note")
    if risk:
        lines.append("")
        lines.append(f"**Risk note:** {risk}")

    if fact_check_notes:
        lines.append("")
        lines.append("## Fact-check notes")
        for note in fact_check_notes:
            lines.append(f"- {note}")

    return "\n".join(lines).strip() + "\n"


def save_draft(
    draft_dir: Path,
    draft: dict[str, Any],
    fact_check_notes: list[str] | None = None,
) -> None:
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (draft_dir / "draft.md").write_text(
        draft_to_markdown(draft, fact_check_notes),
        encoding="utf-8",
    )
