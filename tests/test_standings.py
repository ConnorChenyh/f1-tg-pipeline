from __future__ import annotations

import unittest
from datetime import datetime, timezone

from analyzer.standings import (
    _extract_from_text,
    refresh_team_baseline_from_standings,
)


class StandingsTests(unittest.TestCase):
    def test_extract_driver_standings_from_text(self) -> None:
        html = """
        <html><body>
        1 Kimi Antonelli Mercedes 219
        2 Lewis Hamilton Ferrari 169
        3 George Russell Mercedes 160
        4 Charles Leclerc Ferrari 138
        5 Lando Norris McLaren 128
        6 Max Verstappen Red Bull Racing 109
        7 Oscar Piastri McLaren 92
        8 Isack Hadjar Red Bull Racing 68
        </body></html>
        """

        standings = _extract_from_text(html)

        self.assertEqual(8, len(standings))
        self.assertEqual("Kimi Antonelli", standings[0].name)
        self.assertEqual("Mercedes", standings[0].team)
        self.assertEqual(219, standings[0].points)

    def test_refresh_team_baseline_updates_points_and_positions(self) -> None:
        config = {
            "season_context": {
                "standings_refresh": {"enabled": True},
                "races": [
                    {
                        "round": 11,
                        "name": "Hungarian Grand Prix",
                        "start": "2026-07-24",
                        "end": "2026-07-26",
                    }
                ],
                "team_baseline": {
                    "teams": [
                        {"name": "Mercedes", "constructors_points": 0},
                        {"name": "Ferrari", "constructors_points": 0},
                        {"name": "McLaren", "constructors_points": 0},
                        {"name": "Red Bull Racing", "constructors_points": 0},
                    ]
                },
            }
        }

        from analyzer import standings as standings_module

        original = standings_module.fetch_driver_standings
        try:
            standings_module.fetch_driver_standings = lambda _url, _timeout: _extract_from_text(
                """
                1 Kimi Antonelli Mercedes 219
                2 Lewis Hamilton Ferrari 169
                3 George Russell Mercedes 160
                4 Charles Leclerc Ferrari 138
                5 Lando Norris McLaren 128
                6 Max Verstappen Red Bull Racing 109
                7 Oscar Piastri McLaren 92
                8 Isack Hadjar Red Bull Racing 68
                """
            )
            refreshed = refresh_team_baseline_from_standings(
                config,
                datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc),
            )
        finally:
            standings_module.fetch_driver_standings = original

        self.assertTrue(refreshed)
        teams = config["season_context"]["team_baseline"]["teams"]
        by_name = {team["name"]: team for team in teams}
        self.assertEqual(379, by_name["Mercedes"]["constructors_points"])
        self.assertEqual(1, by_name["Mercedes"]["constructors_position"])
        self.assertEqual(307, by_name["Ferrari"]["constructors_points"])
        self.assertEqual(2, by_name["Ferrari"]["constructors_position"])
        self.assertEqual(177, by_name["Red Bull Racing"]["constructors_points"])
        self.assertEqual(4, by_name["Red Bull Racing"]["constructors_position"])
        self.assertEqual("live Formula1 driver standings", config["season_context"]["team_baseline"]["source"])
        self.assertIn("after R11 Hungarian Grand Prix", config["season_context"]["team_baseline"]["as_of"])


if __name__ == "__main__":
    unittest.main()
