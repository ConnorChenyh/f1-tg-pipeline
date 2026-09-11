from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import run as run_module
from analyzer.run_state import (
    STAGE_COLLECT,
    STAGE_DIGEST,
    STAGE_DELIVERED,
    STAGE_IMAGES,
    STAGE_TOPICS,
    RunState,
    find_resumable_run,
    load_run_state,
    mark_active_run,
    save_run_state,
)
from tests.harness import RunHarness
from tests.test_run_state import _base_config, _stub_post


class ResumeIntegrationTests(unittest.TestCase, RunHarness):
    """Drive run.py twice and prove the second pass does not redo paid work."""

    def test_resume_reuses_draft_without_calling_the_llm_again(self) -> None:
        scripts = self._scripts()
        calls: list = []
        client = self._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            now = datetime.now(timezone.utc)

            first = self._invoke(root, config, client, ["run.py", "--hours", "24"], now)
            self.assertEqual(first, 0)
            self.assertGreater(len(calls), 0, "first pass must call the LLM")
            calls_after_first = len(calls)

            # Simulate a crash before delivery by un-marking the last stages.
            runs = [p for p in (root / "output").iterdir() if p.is_dir()]
            self.assertEqual(len(runs), 1)
            run_dir = runs[0]
            state = load_run_state(run_dir)
            self.assertTrue(state.has(STAGE_DIGEST))

            # Rebuild state as if the run died right after writing the draft.
            crashed = RunState(
                state.run_id,
                state.generated_at,
                state.window_hours,
                [STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST],
            )
            save_run_state(run_dir, crashed)

            second = self._invoke(
                root, config, client, ["run.py", "--hours", "24", "--resume"], datetime.now(timezone.utc)
            )

            self.assertEqual(second, 0)
            self.assertEqual(
                len(calls),
                calls_after_first,
                "resume must not call the LLM again for an already-written draft",
            )

            resumed_state = load_run_state(run_dir)
            self.assertTrue(resumed_state.has(STAGE_IMAGES))
            self.assertTrue(resumed_state.has(STAGE_DELIVERED))

    def test_resume_with_nothing_to_resume_starts_fresh(self) -> None:
        scripts = self._scripts()
        calls: list = []
        client = self._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)

            code = self._invoke(
                root, config, client, ["run.py", "--hours", "24", "--resume"], datetime.now(timezone.utc)
            )

            self.assertEqual(code, 0)
            self.assertGreater(len(calls), 0, "a fresh run still calls the LLM")

    def test_resume_is_ignored_for_mock_runs(self) -> None:
        scripts = self._scripts()
        calls: list = []
        client = self._fake_client(scripts, calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)

            code = self._invoke(
                root,
                config,
                client,
                ["run.py", "--hours", "24", "--mock", "--resume"],
                datetime.now(timezone.utc),
            )

            self.assertEqual(code, 0)
            self.assertEqual(calls, [], "mock must never call the LLM")
            # A mock run writes no resumable pointer.
            self.assertFalse((root / "output" / "active_run.json").exists())


if __name__ == "__main__":
    unittest.main()
