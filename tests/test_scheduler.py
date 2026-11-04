from __future__ import annotations

import unittest
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import patch

from scheduler import next_run_at, run_pipeline


class SchedulerTests(unittest.TestCase):
    def test_recovery_waits_grow_and_consume_shared_deadline(self):
        now = [0.0]
        starts = []
        budgets = []

        def execute(*args, **kwargs):
            starts.append(now[0])
            budgets.append(args[3])
            now[0] += 60
            return 0 if len(starts) == 3 else 1

        def sleep(seconds):
            now[0] += seconds

        with patch("scheduler.time.monotonic", side_effect=lambda: now[0]), \
             patch("scheduler.time.sleep", side_effect=sleep) as wait, \
             patch("scheduler._run_pipeline_once", side_effect=execute):
            self.assertEqual(run_pipeline(Path("config.yaml"), 24, False), 0)
        self.assertEqual(starts, [0, 360, 1020])
        self.assertEqual(budgets, [1800, 1440, 780])
        self.assertEqual([call.args[0] for call in wait.call_args_list], [300, 600])

    def test_insufficient_deadline_does_not_retry_immediately(self):
        with patch("scheduler.time.monotonic", return_value=0), \
             patch("scheduler.time.sleep") as wait, \
             patch("scheduler._run_pipeline_once", return_value=1) as execute, \
             patch("scheduler.load_config", return_value={}), \
             patch("publisher.telegram_text.send_text_to_telegram") as alert:
            self.assertEqual(run_pipeline(Path("config.yaml"), 24, True, 300), 124)
        execute.assert_called_once()
        wait.assert_not_called()
        alert.assert_called_once()

    def test_success_and_timeout_do_not_wait(self):
        for code in (0, 124):
            with self.subTest(code=code), \
                 patch("scheduler.time.sleep") as wait, \
                 patch("scheduler._run_pipeline_once", return_value=code) as execute:
                self.assertEqual(run_pipeline(Path("config.yaml"), 24, False), code)
                execute.assert_called_once()
                wait.assert_not_called()

    def test_next_run_uses_today_when_time_is_future(self) -> None:
        tz = ZoneInfo("Asia/Hong_Kong")
        now = datetime(2026, 7, 13, 8, 30, tzinfo=tz)

        self.assertEqual(
            next_run_at(now, time(9, 0)),
            datetime(2026, 7, 13, 9, 0, tzinfo=tz),
        )

    def test_next_run_rolls_to_tomorrow_when_time_has_passed(self) -> None:
        tz = ZoneInfo("Asia/Hong_Kong")
        now = datetime(2026, 7, 13, 9, 0, tzinfo=tz)

        self.assertEqual(
            next_run_at(now, time(9, 0)),
            datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        )


if __name__ == "__main__":
    unittest.main()
