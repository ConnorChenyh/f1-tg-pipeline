from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from analyzer.season_monitor import (
    build_season_snapshot,
    build_season_update_message,
    load_season_snapshot,
    save_season_snapshot,
)


def _config() -> dict:
    return {
        "season_context": {
            "monitor": {"enabled": True, "state_path": "output/season-state.json"},
            "breaks": [
                {
                    "name": "summer break",
                    "start": "2026-07-27",
                    "end": "2026-08-20",
                }
            ],
            "races": [
                {
                    "round": 11,
                    "name": "Hungarian Grand Prix",
                    "start": "2026-07-24",
                    "end": "2026-07-26",
                },
                {
                    "round": 12,
                    "name": "Dutch Grand Prix",
                    "start": "2026-08-21",
                    "end": "2026-08-23",
                },
                {
                    "round": 13,
                    "name": "Italian Grand Prix",
                    "start": "2026-09-04",
                    "end": "2026-09-06",
                },
            ],
            "team_baseline": {
                "teams": [
                    {
                        "name": "Mercedes",
                        "constructors_position": 1,
                        "constructors_points": 379,
                        "drivers": [
                            {"name": "Kimi Antonelli", "standing": 1, "points": 219}
                        ],
                    }
                ]
            },
        }
    }


class SeasonMonitorTests(unittest.TestCase):
    def test_first_snapshot_is_silent_and_persistent(self) -> None:
        config = _config()
        snapshot = build_season_snapshot(
            config,
            datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc),
            standings_refreshed=True,
        )

        self.assertEqual("break:summer break", snapshot["phase"])
        self.assertIsNone(build_season_update_message(None, snapshot))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_season_snapshot(root, config, snapshot)
            loaded = load_season_snapshot(root, config)

        self.assertEqual(snapshot, loaded)

    def test_completed_race_and_standings_change_create_message(self) -> None:
        config = _config()
        previous = build_season_snapshot(
            config,
            datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc),
            standings_refreshed=True,
        )
        team = config["season_context"]["team_baseline"]["teams"][0]
        team["constructors_points"] = 410
        team["drivers"][0]["points"] = 244
        current = build_season_snapshot(
            config,
            datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc),
            standings_refreshed=True,
        )

        message = build_season_update_message(previous, current)

        self.assertIsNotNone(message)
        self.assertIn("新完赛：R12 Dutch Grand Prix", message)
        self.assertIn("当前/下一站：R13 Italian Grand Prix", message)
        self.assertIn("Mercedes：P1 379分 → P1 410分", message)
        self.assertIn("Kimi Antonelli：P1 219分 → P1 244分", message)
        self.assertIn("休赛期（summer break） → 两站比赛之间", message)

    def test_unchanged_snapshot_does_not_create_message(self) -> None:
        config = _config()
        previous = build_season_snapshot(
            config,
            datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc),
            standings_refreshed=True,
        )
        current = dict(previous)
        current["recorded_at"] = "2026-08-12T04:00:00+00:00"
        current["date"] = "2026-08-12"

        self.assertIsNone(build_season_update_message(previous, current))


if __name__ == "__main__":
    unittest.main()
