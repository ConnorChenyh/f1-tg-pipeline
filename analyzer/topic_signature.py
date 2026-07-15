from __future__ import annotations

import re
from typing import Any

TEXT_RE = re.compile(r"\s+")


def _signature_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("topic_cooldowns", {}) or {}


def topic_cooldown_enabled(config: dict[str, Any]) -> bool:
    return bool(_signature_config(config).get("enabled", True))


def _topic_text(topic: dict[str, Any]) -> str:
    parts = [
        str(topic.get("title_zh") or ""),
        str(topic.get("summary") or ""),
    ]
    for url in topic.get("evidence_urls", []) or []:
        parts.append(str(url))
    for post in topic.get("evidence_posts", []) or []:
        if not isinstance(post, dict):
            continue
        parts.extend(
            [
                str(post.get("title") or ""),
                str(post.get("text") or ""),
                str(post.get("url") or ""),
            ]
        )
    return TEXT_RE.sub(" ", " ".join(parts).lower()).strip()


def _matches_keyword_group(text: str, keywords: list[Any]) -> bool:
    return any(str(keyword).lower() in text for keyword in keywords)


def topic_signature(topic: dict[str, Any], config: dict[str, Any]) -> str:
    if not topic_cooldown_enabled(config):
        return ""

    text = _topic_text(topic)
    if not text:
        return ""

    for rule in _signature_config(config).get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        key = str(rule.get("key") or "").strip()
        groups = rule.get("keyword_groups", []) or []
        if not key or not groups:
            continue
        if all(_matches_keyword_group(text, group or []) for group in groups):
            return key
    return ""


def topic_signature_cooldown_days(signature: str, config: dict[str, Any]) -> int | None:
    if not signature:
        return None
    for rule in _signature_config(config).get("rules", []) or []:
        if isinstance(rule, dict) and rule.get("key") == signature:
            if rule.get("cooldown_days") is not None:
                return int(rule["cooldown_days"])
    default_days = _signature_config(config).get("default_cooldown_days")
    return int(default_days) if default_days is not None else None
