from __future__ import annotations

import json
import logging
from typing import Any

from analyzer.context import RunContext
from generator.deepseek_client import DeepSeekClient
from generator.fact_check import _compact_topic_metadata

logger = logging.getLogger(__name__)

FINAL_REVIEW_SYSTEM_PROMPT = """你是一名中文 F1 终审编辑，负责在发布前做最后一轮质量复核。

你的职责不是重写选题，而是把已经完成事实核查的稿件再检查一遍：
- 中文标点、错别字、语病、主谓宾搭配
- 句子是否造成低级语义误解或动作顺序误读
- 中英文专有名词、车手/车队/部件/系统名是否准确
- 每个条目是否准确总结了对应 topic 的网页原文和证据内容
- 所有事实是否仍能被证据支持；网页 article_content 优先于标题、RSS 摘要和 model_summary

硬性要求：
- 仅返回严格 JSON，不要输出其他文字
- 不得新增证据中没有的新事实、评价、因果或数字
- 不得为了流畅而改变事实强度
- 不确定的技术名词、缩写、升级包名称保留英文原词
- 不要凭常识或训练知识纠正证据里的缩写；证据怎么写就按证据写
- 保持每个条目的信息密度，但必须适合一张图片；删掉重复免责声明和次要背景
- 保持标题、条目数量、ordinal、sources 的结构稳定
"""

FINAL_REVIEW_USER_TEMPLATE = """Final-review this fact-checked F1 digest before publication.

Run context:
{run_context}

Digest title (keep exactly):
{digest_title}

Topic metadata:
{topics_json}

Grounded evidence packet:
{grounding_json}

Current draft JSON:
{draft_json}

Deterministic quality guard issues still visible before final review:
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
  "review_notes": ["终审修改说明1"]
}}

Review rules:
- Keep title exactly as digest title
- Keep the same number of items and preserve each item's ordinal
- Map item 一/二/三... to the grounded topic in the same order; for each item, read all evidence entries under that topic before editing
- When an evidence entry has content_basis=article_content and fetch_status=ok, treat its content as the original article text and the highest-priority source
- Decide whether each headline/content is a faithful summary of that topic's evidence; fix omissions, mistranslations, overstatements, unsupported causal claims, and wrong subject/action/object relations
- Fix punctuation, grammar, awkward Chinese, repeated wording, and ambiguous references
- Preserve useful verified detail. For article-backed or multi-evidence topics, keep content roughly within the configured one-image range; do not expand beyond one image
- If a reviewed item is too short but evidence contains verified context, expand using only evidence-backed background, numbers, quotes, timeline, uncertainty, and significance
- If a reviewed item is too long, compress by removing repeated caveats, secondary color, and redundant background, not by changing facts
- Fix semantic errors caused by compressing separate actions into one sentence
- Re-check time wording: evidence.created_at, RSS publication time, and article publication time are not event time. Remove or soften “今天/周二/昨日/本周/过去24小时内发生” unless evidence.content explicitly supports that event timing
- For event recaps whose event date is unclear or earlier than the run window, phrase them as “报道回顾/报道发布/视频显示”, not as newly happened events
- Re-check names: drivers, team principals, teams, circuits, race names, seasons, car model years
- Re-check technical terms against evidence. If a Chinese translation is not standard or you are not sure, keep the English term
- Preserve acronyms exactly as written in evidence. If the source says “sistema STM”, keep STM; if another evidence text says “FTM blown exhaust system”, do not treat it as higher priority than article_content unless the article itself supports it
- If a claim is unsupported, remove it or downgrade it to 报道称/讨论称/分析认为 according to evidence
- Do not add new facts, jokes, hashtags, sources, metaphors, or dramatic phrasing
- review_notes in Chinese; include one note for each item whose summary correctness or terminology you changed; [] if no changes
"""


def final_review_digest(
    client: DeepSeekClient,
    draft: dict[str, Any],
    topics: list[dict[str, Any]],
    run_context: RunContext,
    digest_title: str,
    grounding: list[dict[str, Any]],
    quality_issues: list[dict[str, str]],
) -> tuple[dict[str, Any], list[str]]:
    user_prompt = FINAL_REVIEW_USER_TEMPLATE.format(
        run_context=run_context.to_prompt_block(),
        digest_title=digest_title,
        topics_json=json.dumps(_compact_topic_metadata(topics), ensure_ascii=False),
        grounding_json=json.dumps(grounding, ensure_ascii=False),
        draft_json=json.dumps(draft, ensure_ascii=False),
        quality_issues_json=json.dumps(quality_issues, ensure_ascii=False),
    )
    reviewed = client.chat_json(client.model_writer, FINAL_REVIEW_SYSTEM_PROMPT, user_prompt)
    if not isinstance(reviewed, dict):
        raise ValueError("final review response is not an object")

    notes = reviewed.get("review_notes", []) or []
    if notes:
        logger.info("Final review corrections: %s", "; ".join(str(n) for n in notes))

    reviewed.pop("review_notes", None)
    reviewed["title"] = digest_title
    return reviewed, notes
