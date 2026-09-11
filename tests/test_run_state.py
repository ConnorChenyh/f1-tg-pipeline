from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run as run_module
from generator.images import RENDER_MEASUREMENTS_FILENAME

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


class RunStateIsolationTests(unittest.TestCase):
    def test_dry_run_does_not_write_published_topic_memory(self) -> None:
        """A collect-only run must not touch story memory (P0-1 regression)."""
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            before = _hashes(root)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module.sys, "argv", ["run.py", "--dry-run", "--hours", "24"]):
                run_context_cls.now.return_value = type(
                    "Ctx",
                    (),
                    {
                        "generated_at": now,
                        "window_hours": 24,
                        "f1_season": 2026,
                        "with_season_context": lambda self, _value: self,
                    },
                )()
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0)
            history, published = _published_state(root)
            self.assertEqual(history, [], "dry run recorded topic history")
            self.assertEqual(published, 0, "dry run recorded published topics")
            self.assertEqual(_changed_state(root, before), [], "dry run touched persistent state")
            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")

    def test_mock_run_leaves_published_memory_untouched(self) -> None:
        """--mock builds a digest but must not claim anything was published."""
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            before = _hashes(root)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module.sys, "argv", ["run.py", "--mock", "--hours", "24"]):
                run_context_cls.now.return_value = type(
                    "Ctx",
                    (),
                    {
                        "generated_at": now,
                        "window_hours": 24,
                        "f1_season": 2026,
                        "with_season_context": lambda self, _value: self,
                    },
                )()
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0)
            history, published = _published_state(root)
            self.assertEqual(history, [], "mock run recorded topic history")
            self.assertEqual(published, 0, "mock run recorded published topics")
            self.assertEqual(
                _changed_state(root, before),
                [],
                "mock run touched persistent state (season snapshot or story memory)",
            )
            # The digest itself must still be produced.
            self.assertTrue((root / "output" / "run1" / "drafts" / "digest" / "draft.json").exists())
            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")


class GuardDegradationTests(unittest.TestCase):
    def test_blocked_draft_is_saved_and_delivery_skipped(self) -> None:
        """A quality-guard rejection must not discard the whole run (P1-1)."""
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            draft = {
                "title": "围场过去24H新闻",
                "hook": "",
                "items": [{"ordinal": "一", "headline": "标题", "content": "正文"}],
                "hashtags": [],
                "sources": ["https://www.motorsport.com/f1/news/example"],
            }
            pushed: list[str] = []

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=object()), \
                 patch.object(run_module, "extract_topics", return_value=[_stub_topic()]), \
                 patch.object(run_module, "enrich_topics_with_evidence", side_effect=lambda topics, _posts: topics), \
                 patch.object(run_module, "generate_digest", return_value=(draft, ["质量检查提示"], ["too_few_items"])), \
                 patch.object(run_module, "push_digest_to_telegram", side_effect=lambda *a, **k: pushed.append("push")), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24", "--push-telegram"]):
                run_context_cls.now.return_value = type(
                    "Ctx",
                    (),
                    {
                        "generated_at": now,
                        "window_hours": 24,
                        "f1_season": 2026,
                        "with_season_context": lambda self, _value: self,
                    },
                )()
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0, "a guard rejection must not fail the run")
            self.assertEqual(pushed, [], "a rejected draft must not be delivered")

            draft_dir = root / "output" / "run1" / "drafts" / "digest"
            self.assertTrue((draft_dir / "draft.json").exists(), "draft must be saved for review")

            meta = json.loads((draft_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertTrue(meta["guard_blocked"])
            self.assertEqual(meta["guard_blocking_codes"], ["too_few_items"])

            # Topics still enter history so the rejected story is not re-picked
            # and re-rejected on the next run.
            history = json.loads((root / "output" / "topic_history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history), 1)
            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")


class GenerateDigestGuardTests(unittest.TestCase):
    """Exercise the real generate_digest, not a mocked return value."""

    def _client(self, digest_payload: dict):
        from generator.deepseek_client import DeepSeekClient

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})

        payloads = [json.dumps(digest_payload)]
        client.chat_json = lambda model, system, user, validator=None: json.loads(payloads.pop(0))
        return client

    def _run(self, draft: dict):
        from generator.digest_writer import generate_digest

        topics = [_stub_topic()]
        client = self._client(draft)
        ctx = SimpleNamespace(to_prompt_block=lambda: "ctx", generated_at=datetime(2026, 9, 11, tzinfo=timezone.utc))
        return generate_digest(
            client,
            topics,
            ctx,
            digest_title="围场过去24H新闻",
            min_items=3,
            max_items=5,
            fact_check_enabled=False,
            final_review_enabled=False,
        )

    def test_blocking_guard_returns_draft_instead_of_raising(self) -> None:
        draft = {
            "items": [{"ordinal": "一", "headline": "标题", "content": "正文"}],
            "sources": ["https://www.motorsport.com/f1/news/example"],
        }

        result_draft, notes, blocking = self._run(draft)

        self.assertEqual(blocking, ["too_few_items"])
        self.assertEqual(result_draft["title"], "围场过去24H新闻")
        self.assertTrue(any("质量检查提示" in note for note in notes))

    def test_clean_draft_reports_no_blockers(self) -> None:
        draft = {
            "items": [
                {"ordinal": "一", "headline": "标题一", "content": "正文一"},
                {"ordinal": "二", "headline": "标题二", "content": "正文二"},
                {"ordinal": "三", "headline": "标题三", "content": "正文三"},
            ],
            "sources": ["https://www.motorsport.com/f1/news/example"],
        }

        _, _, blocking = self._run(draft)

        self.assertEqual(blocking, [])


if __name__ == "__main__":
    unittest.main()


class FullPathWithStubLLMTests(unittest.TestCase):
    """Run the real non-mock pipeline with only the HTTP layer replaced.

    This exercises analyze.topics, generator.digest_writer, quality_guard and
    image generation together: the wiring the unit tests each stub out.
    """

    def _scripts(self) -> dict:
        return {
            "topics": {
                "topics": [
                    {
                        "id": "topic_01",
                        "title_zh": "法拉利西班牙站底板升级",
                        "summary": "法拉利带来新底板以改善低速弯表现。",
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
                        "content": "法拉利在西班牙站带来修订版底板，目标是改善低速弯的下压力表现。" * 6,
                    }
                ],
                "hashtags": ["#F1"],
                "sources": ["https://www.motorsport.com/f1/news/example"],
                "risk_note": "无",
            },
        }

    def test_real_modules_end_to_end(self) -> None:
        real_root = Path(run_module.ROOT)
        real_before = _hashes(real_root)
        scripts = self._scripts()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

            prompts: list[str] = []

            def fake_chat_json(model, system, user, validator=None):
                prompts.append(user)
                payload = scripts["topics"] if '"topics"' in user else scripts["digest"]
                return validator(json.loads(json.dumps(payload))) if validator else payload

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
                from generator.deepseek_client import DeepSeekClient

                stub_client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})
            stub_client.chat_json = fake_chat_json

            from analyzer.context import RunContext as RealRunContext

            real_context = RealRunContext.now(24)

            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as run_context_cls, \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=stub_client), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24"]):
                run_context_cls.now.return_value = real_context
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0, "real module path must complete")
            self.assertTrue(prompts, "the LLM layer was never called")

            draft_dir = root / "output" / "run1" / "drafts" / "digest"
            meta = json.loads((draft_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertFalse(meta["guard_blocked"], f"guard rejected: {meta['guard_blocking_codes']}")
            self.assertEqual(meta["digest_title"], "围场过去24H新闻")

            draft = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["items"][0]["headline"], "法拉利底板升级")

            # Images plus their layout report were produced.
            self.assertTrue((draft_dir / "images" / "cover.png").exists())
            self.assertTrue((draft_dir / "images" / "slide_01.png").exists())
            measurements = json.loads(
                (draft_dir / RENDER_MEASUREMENTS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(len(measurements["items"]), 1)

            self.assertEqual(_hashes(real_root), real_before, "test wrote into the real repository")
