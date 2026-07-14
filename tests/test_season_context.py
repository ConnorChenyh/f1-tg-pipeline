from __future__ import annotations

import unittest
from datetime import datetime, timezone

from analyzer.context import RunContext
from analyzer.season_context import build_season_context_prompt


class SeasonContextTests(unittest.TestCase):
    def test_calendar_context_counts_completed_and_next_race(self) -> None:
        config = {
            "season_context": {
                "enabled": True,
                "cancelled_or_removed": [{"name": "Bahrain Grand Prix", "reason": "not on current calendar"}],
                "races": [
                    {
                        "round": 9,
                        "name": "British Grand Prix",
                        "circuit": "Silverstone",
                        "start": "2026-07-03",
                        "end": "2026-07-05",
                    },
                    {
                        "round": 10,
                        "name": "Belgian Grand Prix",
                        "circuit": "Spa-Francorchamps",
                        "start": "2026-07-17",
                        "end": "2026-07-19",
                    },
                ],
            }
        }

        prompt = build_season_context_prompt(
            config,
            datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc),
        )

        self.assertIn("1 Grands Prix have been completed", prompt)
        self.assertIn("next race is R10 Belgian Grand Prix", prompt)
        self.assertIn("Bahrain Grand Prix", prompt)

    def test_calendar_context_includes_team_baseline(self) -> None:
        config = {
            "season_context": {
                "enabled": True,
                "team_baseline": {
                    "as_of": "after R9",
                    "teams": [
                        {
                            "name": "Ferrari",
                            "chassis": "SF-26",
                            "constructors_position": 2,
                            "constructors_points": 255,
                            "technical": "Uses Flick Tail Mode.",
                        }
                    ],
                },
                "races": [
                    {
                        "round": 9,
                        "name": "British Grand Prix",
                        "circuit": "Silverstone",
                        "start": "2026-07-03",
                        "end": "2026-07-05",
                    }
                ],
            }
        }

        prompt = build_season_context_prompt(
            config,
            datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc),
        )

        self.assertIn("Current big-four car and performance baseline (after R9)", prompt)
        self.assertIn("Ferrari: chassis SF-26; P2 in Constructors with 255 points", prompt)
        self.assertIn("Uses Flick Tail Mode.", prompt)
        self.assertNotIn("Mode..", prompt)

    def test_run_context_includes_season_context(self) -> None:
        run_context = RunContext(
            generated_at=datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc),
            window_hours=24,
            f1_season=2026,
        ).with_season_context("Season calendar context:\n- Test context.")

        self.assertIn("Season calendar context", run_context.to_prompt_block())


if __name__ == "__main__":
    unittest.main()
