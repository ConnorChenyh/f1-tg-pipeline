"""Shared fixtures for tests that drive run.py's main() end to end.

Deliberately not a TestCase: importing fixtures from a test module makes
unittest discover collect that module's tests again in every importer, which is
why the suite previously reported 172 executions for 169 unique tests.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import run as run_module



STATE_FILES = (
    "topic_history.json",
    "story_memory.sqlite3",
    "season_context_state.json",
    "pending_telegram_deliveries.json",
    "standings_cache.json",
    "active_run.json",
)


def _published_state(root: Path) -> tuple[list, int]:
    """(topic_history entries, published_topics rows) — what "published" means."""
    import sqlite3

    history_path = root / "output" / "topic_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []

    db_path = root / "output" / "story_memory.sqlite3"
    rows = 0
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            try:
                rows = conn.execute("SELECT COUNT(*) FROM published_topics").fetchone()[0]
            except sqlite3.OperationalError:
                rows = 0
    return history, rows


MISSING = "<absent>"


def _hashes(root: Path) -> dict[str, str]:
    """Map every watched state file to its hash, recording absence explicitly.

    A newly created state file must show up as a difference, so absence is
    encoded rather than skipped.
    """
    digests: dict[str, str] = {}
    for name in STATE_FILES:
        path = root / "output" / name
        digests[name] = hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else MISSING
    return digests


def _changed_state(root: Path, before: dict[str, str]) -> list[str]:
    return sorted(name for name, digest in _hashes(root).items() if before.get(name) != digest)


def _base_config(root: Path) -> dict:
    return {
        "window_hours": 24,
        "heat_threshold": 55,
        "digest": {"title": "围场过去24H新闻", "min_items": 1, "max_items": 5},
        "topic_history": {"enabled": True, "dedupe_days": 7, "path": "output/topic_history.json"},
        "story_db": {"enabled": True, "path": "output/story_memory.sqlite3", "retention_days": 30},
        "season_context": {"enabled": True, "monitor": {"enabled": True, "state_path": "output/season_context_state.json"}},
        "shortlist": {"enabled": True, "limit": 80},
        "article_fetch": {"enabled": False, "delay_sec": 0},
        "telegram": {"pending_deliveries_path": "output/pending_telegram_deliveries.json"},
        "deepseek": {"fact_check_enabled": False, "final_review_enabled": False},
    }


def _stub_post(now: datetime):
    from collectors.base import PostItem

    return PostItem(
        source="rss",
        text="Ferrari brings a revised floor to the Spanish Grand Prix weekend.",
        title="Ferrari floor update",
        url="https://www.motorsport.com/f1/news/example",
        created_at=now,
        likes=10,
        replies=1,
        retweets=0,
        raw_score=5.0,
    )


def _stub_topic() -> dict:
    return {
        "id": "topic_01",
        "title_zh": "法拉利西班牙站底板升级",
        "summary": "法拉利带来新底板。",
        "heat_score": 80,
        "evidence_urls": ["https://www.motorsport.com/f1/news/example"],
        "evidence_posts": [
            {
                "url": "https://www.motorsport.com/f1/news/example",
                "source": "rss",
                "title": "Ferrari floor update",
                "text": "Ferrari brings a revised floor.",
                "created_at": "",
                "fetch_status": "skipped",
                "article_content": "",
            }
        ],
    }


class RunHarness:
    """Drives run.py with stubbed collectors, LLM and season context."""

    def _fake_client(self, scripts: dict, calls: list) -> object:
        def fake_chat_json(model, system, user, validator=None, stage="unknown"):
            calls.append(user)
            payload = scripts["topics"] if '"topics"' in user else scripts["digest"]
            return validator(json.loads(json.dumps(payload))) if validator else payload

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            from generator.deepseek_client import DeepSeekClient

            client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})
        client.chat_json = fake_chat_json
        return client

    def _scripts(self) -> dict:
        return {
            "topics": {
                "topics": [
                    {
                        "id": "topic_01",
                        "title_zh": "法拉利西班牙站底板升级",
                        "summary": "法拉利带来新底板。",
                        "heat_score": 82,
                        "evidence_urls": ["https://www.motorsport.com/f1/news/example"],
                        "publish_recommendation": "publish",
                        "skip_reason": None,
                    }
                ]
            },
            "digest": {
                "title": "围场过去24H新闻",
                "hook": "过去24小时，围场有这些值得关注的消息：",
                "items": [
                    {
                        "ordinal": "一",
                        "headline": "法拉利底板升级",
                        "content": "法拉利在西班牙站带来修订版底板。" * 20,
                    }
                ],
                "hashtags": ["#F1"],
                "sources": ["https://www.motorsport.com/f1/news/example"],
                "risk_note": "无",
            },
        }

    def _invoke(self, root: Path, config: dict, client, argv: list[str], now: datetime) -> int:
        from analyzer.context import RunContext as RealRunContext

        collector_posts = [_stub_post(now)]
        with patch.object(run_module, "ROOT", root), \
             patch.object(run_module, "load_config", return_value=config), \
             patch.object(run_module, "collect_reddit", return_value=[]), \
             patch.object(run_module, "collect_rss", return_value=collector_posts), \
             patch.object(run_module, "collect_twitter", return_value=[]), \
             patch.object(run_module, "RunContext") as run_context_cls, \
             patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
             patch.object(run_module, "build_season_context_prompt", return_value=""), \
             patch.object(run_module, "build_season_snapshot", return_value={}), \
             patch.object(run_module, "load_season_snapshot", return_value=None), \
             patch.object(run_module, "build_season_update_message", return_value=None), \
             patch.object(run_module, "DeepSeekClient", return_value=client), \
             patch.object(run_module.sys, "argv", argv):
            run_context_cls.now.return_value = RealRunContext.now(24)
            return run_module.main()
