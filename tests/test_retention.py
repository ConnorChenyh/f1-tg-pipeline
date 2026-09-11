from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer.retention import prune_output_runs

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _make_run(root: Path, name: str, age_days: float) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "raw_posts.json").write_text("[]", encoding="utf-8")
    # Set the timestamp last: writing a file updates the directory mtime.
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(run, (stamp, stamp))
    return run


def _config(**overrides) -> dict:
    base = {"enabled": True, "keep_days": 14, "keep_min_runs": 2}
    base.update(overrides)
    return {"output_retention": base}


class RetentionTests(unittest.TestCase):
    def test_old_runs_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-08-01_120000", 41)
            _make_run(root, "2026-09-10_120000", 1)

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, ["2026-08-01_120000"])
            self.assertFalse((root / "2026-08-01_120000").exists())
            self.assertTrue((root / "2026-09-10_120000").exists())

    def test_recent_runs_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-09-10_120000", 1)
            _make_run(root, "2026-09-09_120000", 2)

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, [])
            self.assertTrue((root / "2026-09-10_120000").exists())

    def test_keep_min_runs_protects_old_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("2026-08-01_120000", "2026-08-02_120000", "2026-08-03_120000"):
                _make_run(root, name, 40)

            removed = prune_output_runs(root, _config(keep_min_runs=2), NOW)

            # The two newest survive even though every one of them is old.
            self.assertEqual(removed, ["2026-08-01_120000"])
            self.assertTrue((root / "2026-08-03_120000").exists())
            self.assertTrue((root / "2026-08-02_120000").exists())

    def test_pending_delivery_reference_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-08-01_120000", 41)
            (root / "pending_telegram_deliveries.json").write_text(
                json.dumps({"deliveries": [{"output_dir": "output/2026-08-01_120000"}]}),
                encoding="utf-8",
            )

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, [], "a queued digest must not have its run deleted")
            self.assertTrue((root / "2026-08-01_120000").exists())

    def test_active_run_reference_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-08-01_120000", 41)
            (root / "active_run.json").write_text(
                json.dumps({"output_dir": "2026-08-01_120000"}), encoding="utf-8"
            )

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, [], "an unfinished run must stay resumable")
            self.assertTrue((root / "2026-08-01_120000").exists())

    def test_shared_state_files_are_never_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = (
                "topic_history.json",
                "story_memory.sqlite3",
                "story_memory.sqlite3-wal",
                "season_context_state.json",
                "standings_cache.json",
                "pending_telegram_deliveries.json",
                "active_run.json",
            )
            for name in names:
                (root / name).write_text("{}", encoding="utf-8")

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, [])
            for name in names:
                self.assertTrue((root / name).exists(), f"{name} must survive retention")

    def test_non_pipeline_directories_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment = root / "bbc_verstappen_20260911"
            experiment.mkdir()
            (experiment / "page_01.png").write_bytes(b"x")
            stamp = (NOW - timedelta(days=200)).timestamp()
            os.utime(experiment, (stamp, stamp))

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, [])
            self.assertTrue(experiment.exists(), "hand-made experiment folders must survive")

    def test_disabled_retention_removes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-01-01_120000", 250)

            removed = prune_output_runs(root, _config(enabled=False, keep_min_runs=0), NOW)

            self.assertEqual(removed, [])
            self.assertTrue((root / "2026-01-01_120000").exists())

    def test_missing_output_root_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            removed = prune_output_runs(Path(tmp) / "nope", _config(), NOW)
            self.assertEqual(removed, [])

    def test_corrupt_reference_file_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-08-01_120000", 41)
            (root / "active_run.json").write_text("{broken", encoding="utf-8")

            # An unreadable reference is treated as no reference; the run is old,
            # so it is removed, but the call must not raise.
            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, ["2026-08-01_120000"])

    def test_returns_the_names_it_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_run(root, "2026-07-01_120000", 70)
            _make_run(root, "2026-07-02_120000", 69)

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, ["2026-07-02_120000", "2026-07-01_120000"])

    def test_only_timestamped_run_directories_are_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("notes", "2026-08-01", "2026-08-01_1200", "20260801_120000"):
                path = root / name
                path.mkdir()
                stamp = (NOW - timedelta(days=90)).timestamp()
                os.utime(path, (stamp, stamp))

            removed = prune_output_runs(root, _config(keep_min_runs=0), NOW)

            self.assertEqual(removed, [], "only timestamped pipeline runs are candidates")


if __name__ == "__main__":
    unittest.main()
