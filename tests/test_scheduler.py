from __future__ import annotations

import unittest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from scheduler import next_run_at


class SchedulerTests(unittest.TestCase):
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
