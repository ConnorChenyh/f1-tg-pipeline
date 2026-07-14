from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from analyzer.context import RunContext
from generator.deepseek_client import DeepSeekClient
from generator.evidence_pack import build_digest_grounding
from generator.fact_check import fact_check_digest
from generator.final_review import final_review_digest
from generator.prompts import DIGEST_SYSTEM_PROMPT
from generator.quality_guard import blocking_issues, issue_dicts, issue_summary, validate_digest

logger = logging.getLogger(__name__)

DIGEST_USER_TEMPLATE = """Create ONE digest post that merges these hot topics.

Run context:
{run_context}

Digest title (use exactly):
{digest_title}

Grounded topics JSON:
{grounding_json}

Return JSON with this exact shape:
{{
  "title": "{digest_title}",
  "hook": "开头1-2句总起",
  "items": [
    {{
      "ordinal": "一",
      "headline": "小标题（简短）",
      "content": "5-7句说明，适合完整放进一张文字图片，仅基于证据"
    }},
    {{
      "ordinal": "二",
      "headline": "...",
      "content": "..."
    }}
  ],
  "hashtags": ["#F1", "#标签"],
  "sources": ["url1", "url2"],
  "risk_note": "风险说明，没有风险写无"
}}

Rules:
- Include {min_items} to {max_items} items
- ordinals must be Chinese numerals: 一、二、三、四、五
- Each item covers ONE distinct topic from the input
- Each item content should be {item_min_chars}-{item_max_chars} Chinese characters, target around {item_target_chars}
- Never exceed {item_max_chars} Chinese characters for item content; it must fit on one image
- If a topic has article_content or at least two evidence entries, avoid a short summary; add verified background, involved parties, direct quote/number when available, uncertainty level, and why it matters
- If evidence is genuinely thin or social-only, use fewer words rather than inventing details, but still explain clearly what is known, what is unknown, and why the uncertainty matters
- Avoid repetitive caveats; state uncertainty once, then move on
- Write enough detail for a standalone text image: background, key fact, direct quote/number when available, context, uncertainty, and why it matters
- Use each evidence.content as the source of truth; model_summary is secondary context only
- When content_basis is article_content, summarize/translate from article_content, not the title or RSS text
- When source_note says social post only or RSS/title snippet only, keep claims explicitly anchored (视频显示/帖文称/报道标题称) and do not add detail
- Preserve event sequence accurately; do not merge separate actions into one sentence
- Never write that someone drove a car onto a balcony unless the evidence literally says that
- Translate champion/title/win/podium carefully; avoid “新科冠军” unless the evidence explicitly supports that exact meaning
- For technical acronyms, aero parts, power-unit systems, and named upgrades, keep the original English term unless a reliable common Chinese name is evident in the evidence
- Do not force-translate uncertain technical terms such as “STM blown exhaust”; prefer English or English plus a brief Chinese explanation
- Avoid exaggerated idioms such as 军令状、暗流涌动、最大谜题、亲自下场、满天飞
- ONLY use facts from evidence.content; label speculation as 讨论/传闻/分析
- sources should collect unique evidence URLs from all topics
- Do not repeat the same story twice
"""


def generate_digest(
    client: DeepSeekClient,
    topics: list[dict[str, Any]],
    run_context: RunContext,
    digest_title: str,
    min_items: int = 3,
    max_items: int = 5,
    item_min_chars: int = 240,
    item_target_chars: int = 300,
    item_max_chars: int = 380,
    fact_check_enabled: bool = True,
    final_review_enabled: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    grounding = build_digest_grounding(topics)
    user_prompt = DIGEST_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        digest_title=digest_title,
        grounding_json=json.dumps(grounding, ensure_ascii=False),
        min_items=min_items,
        max_items=max_items,
        item_min_chars=item_min_chars,
        item_target_chars=item_target_chars,
        item_max_chars=item_max_chars,
    )
    draft = client.chat_json(client.model_writer, DIGEST_SYSTEM_PROMPT, user_prompt)
    if not isinstance(draft, dict):
        raise ValueError("digest response is not an object")

    draft["title"] = digest_title
    quality_issues = validate_digest(
        draft,
        topics,
        min_items=min_items,
        max_items=max_items,
        min_item_chars=item_min_chars,
        max_item_chars=item_max_chars,
    )

    fact_check_notes: list[str] = []
    if fact_check_enabled:
        draft, fact_check_notes = fact_check_digest(
            client,
            draft,
            topics,
            run_context,
            digest_title,
            grounding=grounding,
            quality_issues=issue_dicts(quality_issues),
        )

    review_notes: list[str] = []
    if final_review_enabled:
        review_quality_issues = validate_digest(
            draft,
            topics,
            min_items=min_items,
            max_items=max_items,
            min_item_chars=item_min_chars,
            max_item_chars=item_max_chars,
        )
        draft, review_notes = final_review_digest(
            client,
            draft,
            topics,
            run_context,
            digest_title,
            grounding=grounding,
            quality_issues=issue_dicts(review_quality_issues),
        )

    final_quality_issues = validate_digest(
        draft,
        topics,
        min_items=min_items,
        max_items=max_items,
        min_item_chars=item_min_chars,
        max_item_chars=item_max_chars,
    )
    blockers = blocking_issues(final_quality_issues)
    if blockers:
        raise ValueError(f"digest quality guard failed: {issue_summary(blockers)}")

    for note in review_notes:
        fact_check_notes.append(f"终审提示：{note}")

    for issue in final_quality_issues:
        fact_check_notes.append(f"质量检查提示：{issue.message}")

    return draft, fact_check_notes


def digest_to_markdown(draft: dict[str, Any], fact_check_notes: list[str] | None = None) -> str:
    lines = [
        f"# {draft.get('title', '')}",
        "",
        draft.get("hook", ""),
        "",
    ]

    for item in draft.get("items", []):
        ordinal = item.get("ordinal", "")
        headline = item.get("headline", "")
        content = item.get("content", "")
        prefix = f"{ordinal}、" if ordinal and not headline.startswith(ordinal) else ""
        lines.append(f"{prefix}{headline}")
        lines.append("")
        lines.append(content)
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


def save_digest(draft_dir: Path, draft: dict[str, Any], fact_check_notes: list[str] | None = None) -> None:
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (draft_dir / "draft.md").write_text(
        digest_to_markdown(draft, fact_check_notes),
        encoding="utf-8",
    )
