from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("replay_digest", ROOT / "scripts" / "replay_digest.py")
replay_digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_digest)

TOPICS = [{"id": "topic_01", "title_zh": "话题一", "evidence_posts": []},
          {"id": "topic_02", "title_zh": "话题二", "evidence_posts": []}]
OLD_DRAFT = {"title": "围场过去24H新闻", "items": [
    {"ordinal": "一", "headline": "旧标题一", "content": "旧正文一"},
    {"ordinal": "二", "headline": "旧标题二", "content": "旧正文二"},
]}
NEW_DRAFT = {"title": "围场过去24H新闻", "items": [
    {"ordinal": "一", "headline": "新标题一", "content": "新正文一"},
    {"ordinal": "二", "headline": "新标题二", "content": "新正文二"},
]}
GENERATED_AT = "2026-10-05T04:00:04+00:00"


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _source_run(root: Path, *, season: bool = True) -> Path:
    run_dir = root / "2026-10-05_120004"
    _write(run_dir / "drafts" / "digest" / "meta.json", {
        "digest_title": "围场过去24H新闻",
        "topics": TOPICS,
        "fact_check_notes": ["修正引语方向", "终审提示：改写直译习语"],
        "run_context": {"generated_at": GENERATED_AT, "f1_season": 2026, "window_hours": 24},
    })
    _write(run_dir / "drafts" / "digest" / "draft.json", OLD_DRAFT)
    if season:
        _write(run_dir / "season_snapshot.json", {"config": {"snapshot": "original"}, "standings_refreshed": True})
    return run_dir


class _FakeClient:
    usage = None


class ReplayDigestTests(unittest.TestCase):
    def test_replay_reuses_original_inputs_and_writes_only_to_out_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = _source_run(tmp_path)
            out_dir = tmp_path / "replay"
            before = {p for p in tmp_path.rglob("*")}
            config = {"digest": {"min_items": 4, "max_items": 5, "item_max_chars": 500},
                      "season_context": {"snapshot": "live"}}

            with patch.object(replay_digest, "build_season_context_prompt", return_value="SEASON") as season, \
                    patch.object(replay_digest, "generate_digest",
                                 return_value=(NEW_DRAFT, ["修正积分口径"], [])) as generate:
                result = replay_digest.replay(source, out_dir, config, _FakeClient())

            # The original season snapshot and run time drive the prompt, not live data.
            self.assertEqual(season.call_args.args[0]["season_context"], {"snapshot": "original"})
            self.assertFalse(config["_runtime"]["persist"])
            args, kwargs = generate.call_args
            self.assertEqual(args[1], TOPICS)
            context = args[2]
            self.assertEqual(context.generated_at, datetime(2026, 10, 5, 4, 0, 4, tzinfo=timezone.utc))
            self.assertEqual(context.season_context, "SEASON")
            # min_items is capped to the topics the source run actually had.
            self.assertEqual(kwargs["min_items"], 2)
            self.assertEqual(kwargs["max_items"], 5)

            meta = json.loads((out_dir / "drafts" / "digest" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["run_context"]["generated_at"], GENERATED_AT)
            self.assertFalse(meta["guard_blocked"])
            comparison = result["comparison"].read_text(encoding="utf-8")
            self.assertIn("旧标题一", comparison)
            self.assertIn("新标题一", comparison)
            self.assertIn("| 事实核查 | 1 | 1 |", comparison)
            self.assertIn("| 终审 | 1 | 0 |", comparison)

            created = {p for p in tmp_path.rglob("*")} - before
            self.assertTrue(all(out_dir == p or out_dir in p.parents for p in created))

    def test_blocked_replay_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _source_run(Path(tmp))
            with patch.object(replay_digest, "build_season_context_prompt", return_value=""), \
                    patch.object(replay_digest, "generate_digest", return_value=(NEW_DRAFT, [], ["too_few_items"])):
                result = replay_digest.replay(source, Path(tmp) / "out", {}, _FakeClient())
            self.assertTrue(result["meta"]["guard_blocked"])
            self.assertIn("拦截 too_few_items", result["comparison"].read_text(encoding="utf-8"))

    def test_missing_season_snapshot_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _source_run(Path(tmp), season=False)
            with self.assertRaises(FileNotFoundError):
                replay_digest.load_source(source)

    def test_push_marks_title_as_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts" / "digest"
            _write(draft_dir / "draft.json", NEW_DRAFT)
            _write(draft_dir / "meta.json", {"digest_title": "围场过去24H新闻",
                                             "run_context": {"generated_at": GENERATED_AT}})
            with patch.object(replay_digest, "push_digest_to_telegram", return_value={}) as push:
                replay_digest.push_replay(draft_dir, {})
            push.assert_called_once()
            sent = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
            self.assertTrue(sent["telegram_title"].startswith("【重跑对比】围场过去24H新闻"))


if __name__ == "__main__":
    unittest.main()
