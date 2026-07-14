from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from generator.evidence_pack import collect_evidence_source_urls, normalize_source_url


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


EVENT_CONFLATION_PATTERNS = [
    (
        "event_conflation_balcony",
        re.compile(r"驾驶[^。！？，；、\n]{0,35}(?:登上|走上|开上|冲上)[^。！？，；、\n]{0,25}阳台"),
        "可能把爬山赛驾驶、停车、步行上阳台压缩成了一个错误动作；需要拆成独立动作。",
    ),
]

STYLE_RISK_PATTERNS = [
    (
        "ambiguous_new_champion",
        re.compile(r"新科冠军"),
        "“新科冠军”容易把分站冠军、年度冠军或旧赛季冠军混淆；按证据改成更具体称谓。",
    ),
    (
        "overstated_military_idiom",
        re.compile(r"军令状"),
        "“军令状”会增加证据中没有的承诺强度；建议改成“目标”或“时间表”。",
    ),
    (
        "overdramatized_tone",
        re.compile(r"暗流涌动|最大的谜题|谜题|亲自下场|满天飞"),
        "表达偏戏剧化，容易放大不确定信息；建议改成克制新闻语气。",
    ),
]

TECHNICAL_TERM_PATTERNS = [
    (
        "possibly_overtranslated_technical_term",
        re.compile(r"[A-Z]{2,}[A-Za-z0-9-]*[\u4e00-\u9fff]{2,}(?:系统|尾翼|底板|排气|悬挂|扩散器|升级)"),
        "疑似把英文缩写或专业系统名硬翻成中文；请核对证据中的英文原词，没把握时保留英文。",
    ),
]

SOURCE_ANCHOR_PATTERNS = [
    (
        "unanchored_social_video_claim",
        re.compile(r"(?<!视频显示，)(?<!视频显示)(?<!帖文称，)(?<!帖文称)(竖起了中指|竖中指)"),
        "社交视频类细节应标注“视频显示/帖文称”，避免写成已充分核实的新闻事实。",
    ),
]


def _draft_text_fields(draft: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for key in ("title", "hook", "risk_note"):
        value = draft.get(key)
        if isinstance(value, str) and value:
            fields.append((key, value))

    for index, item in enumerate(draft.get("items", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        for key in ("headline", "content"):
            value = item.get(key)
            if isinstance(value, str) and value:
                fields.append((f"items[{index}].{key}", value))

    for index, paragraph in enumerate(draft.get("body", []) or [], start=1):
        if isinstance(paragraph, str) and paragraph:
            fields.append((f"body[{index}]", paragraph))

    return fields


def _append_pattern_issues(
    issues: list[QualityIssue],
    fields: list[tuple[str, str]],
    patterns: list[tuple[str, re.Pattern[str], str]],
    severity: str,
) -> None:
    for location, text in fields:
        for code, pattern, message in patterns:
            if pattern.search(text):
                issues.append(QualityIssue(code, severity, location, message))


def _append_source_anchor_issues(
    issues: list[QualityIssue],
    fields: list[tuple[str, str]],
) -> None:
    for location, text in fields:
        for code, pattern, message in SOURCE_ANCHOR_PATTERNS:
            if not pattern.search(text):
                continue
            if re.search(r"(?:视频|帖文|社交媒体)[^。！？\n]{0,30}(?:竖起了中指|竖中指)", text):
                continue
            issues.append(QualityIssue(code, "warn", location, message))


def _validate_sources(draft: dict[str, Any], topics: list[dict[str, Any]] | None) -> list[QualityIssue]:
    if topics is None:
        return []

    issues: list[QualityIssue] = []
    expected = collect_evidence_source_urls(topics)
    actual_sources = draft.get("sources", []) or []

    if not actual_sources:
        return [
            QualityIssue(
                "missing_sources",
                "error",
                "sources",
                "草稿没有 sources，无法人工核对事实来源。",
            )
        ]

    for index, source in enumerate(actual_sources, start=1):
        normalized = normalize_source_url(str(source))
        if normalized and normalized not in expected:
            issues.append(
                QualityIssue(
                    "unknown_source",
                    "error",
                    f"sources[{index}]",
                    "sources 中包含不在 evidence_urls/evidence_posts 里的链接。",
                )
            )

    return issues


def _topic_has_rich_evidence(topic: dict[str, Any] | None) -> bool:
    if not topic:
        return False
    evidence_posts = topic.get("evidence_posts", []) or []
    if len(evidence_posts) >= 2:
        return True
    return any(
        post.get("fetch_status") == "ok" and (post.get("article_content") or "").strip()
        for post in evidence_posts
        if isinstance(post, dict)
    )


def _validate_item_lengths(
    draft: dict[str, Any],
    topics: list[dict[str, Any]] | None,
    min_item_chars: int | None,
) -> list[QualityIssue]:
    if min_item_chars is None:
        return []

    items = draft.get("items", [])
    if not isinstance(items, list):
        return []

    issues: list[QualityIssue] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        topic = topics[index - 1] if topics and index - 1 < len(topics) else None
        if len(content) < min_item_chars and _topic_has_rich_evidence(topic):
            issues.append(
                QualityIssue(
                    "too_short_rich_evidence_item",
                    "warn",
                    f"items[{index}].content",
                    f"该条有多条证据或网页原文，但正文只有 {len(content)} 字，低于配置下限 {min_item_chars}；应基于证据补足背景、数字、时间线、不确定性和意义。",
                )
            )

    return issues


def validate_digest(
    draft: dict[str, Any],
    topics: list[dict[str, Any]] | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
    min_item_chars: int | None = None,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    fields = _draft_text_fields(draft)

    items = draft.get("items", [])
    if not isinstance(items, list):
        issues.append(QualityIssue("items_not_list", "error", "items", "items 必须是数组。"))
    else:
        if min_items is not None and len(items) < min_items:
            issues.append(
                QualityIssue(
                    "too_few_items",
                    "error",
                    "items",
                    f"草稿条目数少于配置要求的 {min_items} 条。",
                )
            )
        if max_items is not None and len(items) > max_items:
            issues.append(
                QualityIssue(
                    "too_many_items",
                    "error",
                    "items",
                    f"草稿条目数多于配置要求的 {max_items} 条。",
                )
            )

    _append_pattern_issues(issues, fields, EVENT_CONFLATION_PATTERNS, "error")
    _append_pattern_issues(issues, fields, TECHNICAL_TERM_PATTERNS, "warn")
    _append_pattern_issues(issues, fields, STYLE_RISK_PATTERNS, "warn")
    _append_source_anchor_issues(issues, fields)
    issues.extend(_validate_item_lengths(draft, topics, min_item_chars))
    issues.extend(_validate_sources(draft, topics))
    return issues


def issue_dicts(issues: list[QualityIssue]) -> list[dict[str, str]]:
    return [issue.to_dict() for issue in issues]


def blocking_issues(issues: list[QualityIssue]) -> list[QualityIssue]:
    return [issue for issue in issues if issue.severity == "error"]


def issue_summary(issues: list[QualityIssue]) -> str:
    return "; ".join(f"{issue.code}@{issue.location}" for issue in issues)
