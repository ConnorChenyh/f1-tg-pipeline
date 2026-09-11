from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer.standings import (
    _extract_from_text,
    _extract_team_standings_from_text,
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

    def test_extract_team_standings_from_text(self) -> None:
        standings = _extract_team_standings_from_text(
            "1 Mercedes 425 2 Ferrari 338 3 McLaren 263 4 Red Bull Racing 186"
        )

        self.assertEqual(4, len(standings))
        self.assertEqual("Red Bull Racing", standings[-1].name)
        self.assertEqual(4, standings[-1].standing)
        self.assertEqual(186, standings[-1].points)

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
        original_team = standings_module.fetch_team_standings
        try:
            standings_module.fetch_driver_standings = lambda _url, _timeout, *_a, **_k: _extract_from_text(
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
            standings_module.fetch_team_standings = lambda _url, _timeout, *_a, **_k: _extract_team_standings_from_text(
                "1 Mercedes 425 2 Ferrari 338 3 McLaren 263 4 Red Bull Racing 186"
            )
            refreshed = refresh_team_baseline_from_standings(
                config,
                datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc),
            )
        finally:
            standings_module.fetch_driver_standings = original
            standings_module.fetch_team_standings = original_team

        self.assertTrue(refreshed)
        teams = config["season_context"]["team_baseline"]["teams"]
        by_name = {team["name"]: team for team in teams}
        self.assertEqual(425, by_name["Mercedes"]["constructors_points"])
        self.assertEqual(1, by_name["Mercedes"]["constructors_position"])
        self.assertEqual(338, by_name["Ferrari"]["constructors_points"])
        self.assertEqual(2, by_name["Ferrari"]["constructors_position"])
        self.assertEqual(186, by_name["Red Bull Racing"]["constructors_points"])
        self.assertEqual(4, by_name["Red Bull Racing"]["constructors_position"])
        self.assertEqual("live Formula1 driver and team standings", config["season_context"]["team_baseline"]["source"])
        self.assertIn("after R11 Hungarian Grand Prix", config["season_context"]["team_baseline"]["as_of"])


if __name__ == "__main__":
    unittest.main()


class DriverNameCleanupTests(unittest.TestCase):
    """formula1.com renders 'Kimi Antonelli ANT ITA'; the codes must not leak."""

    def test_strips_driver_code_and_nationality(self) -> None:
        from analyzer.standings import _clean_driver_name

        self.assertEqual(_clean_driver_name("Kimi Antonelli ANT"), "Kimi Antonelli")
        self.assertEqual(_clean_driver_name("Kimi Antonelli ANT ITA"), "Kimi Antonelli")
        self.assertEqual(_clean_driver_name("Max Verstappen VER NED"), "Max Verstappen")

    def test_leaves_a_plain_name_alone(self) -> None:
        from analyzer.standings import _clean_driver_name

        self.assertEqual(_clean_driver_name("Kimi Antonelli"), "Kimi Antonelli")
        self.assertEqual(_clean_driver_name("  Lewis   Hamilton  "), "Lewis Hamilton")

    def test_keeps_multi_word_and_apostrophe_names(self) -> None:
        from analyzer.standings import _clean_driver_name

        self.assertEqual(_clean_driver_name("Andrea Kimi Antonelli ANT"), "Andrea Kimi Antonelli")
        self.assertEqual(_clean_driver_name("Jean-Éric Vergne JEV FRA"), "Jean-Éric Vergne")

    def test_both_parsers_agree_on_clean_names(self) -> None:
        from analyzer.standings import _extract_from_tables

        html = """
        <table><tr><th>1</th><td>Kimi Antonelli</td><td>ANT</td><td>ITA</td><td>Mercedes</td><td>267</td></tr></table>
        """
        table_rows = _extract_from_tables(html)
        text_rows = _extract_from_text(
            "<html><body>1 Kimi Antonelli ANT ITA Mercedes 267</body></html>"
        )

        self.assertEqual([r.name for r in table_rows], ["Kimi Antonelli"])
        self.assertEqual([r.name for r in text_rows], ["Kimi Antonelli"])


class SeasonPhaseLabelTests(unittest.TestCase):
    """as_of must follow the calendar instead of a hardcoded config string."""

    def _season(self) -> dict:
        return {
            "races": [
                {"round": 11, "name": "Hungarian Grand Prix", "start": "2026-07-24", "end": "2026-07-26"},
                {"round": 13, "name": "Italian Grand Prix", "start": "2026-09-04", "end": "2026-09-06"},
                {"round": 14, "name": "Spanish Grand Prix", "start": "2026-09-11", "end": "2026-09-13"},
            ]
        }

    def test_label_tracks_the_latest_completed_race(self) -> None:
        from analyzer.standings import _season_phase_label

        label = _season_phase_label(self._season(), datetime(2026, 9, 11, tzinfo=timezone.utc))

        self.assertIn("after R13 Italian Grand Prix", label)
        self.assertNotIn("R11", label)
        self.assertNotIn("summer break", label)

    def test_label_handles_a_pre_season_date(self) -> None:
        from analyzer.standings import _season_phase_label

        label = _season_phase_label(self._season(), datetime(2026, 1, 5, tzinfo=timezone.utc))

        self.assertIn("before the first race", label)

    def test_snapshot_fallback_still_advances_the_phase_label(self) -> None:
        """A failed refresh must not leave the prompt claiming an old round."""
        from analyzer import standings as standings_module

        config = {
            "season_context": {
                "standings_refresh": {
                    "enabled": True,
                    "cache_max_age_sec": 0,
                },
                "races": self._season()["races"],
                "team_baseline": {"as_of": "after R11 Hungarian Grand Prix", "teams": []},
            }
        }
        original = standings_module.fetch_driver_standings

        def boom(_url, _timeout):
            raise RuntimeError("network down")

        try:
            standings_module.fetch_driver_standings = boom
            standings_module.fetch_team_standings = boom
            refreshed = standings_module.refresh_team_baseline_from_standings(
                config, datetime(2026, 9, 11, tzinfo=timezone.utc)
            )
        finally:
            standings_module.fetch_driver_standings = original

        self.assertFalse(refreshed)
        as_of = config["season_context"]["team_baseline"]["as_of"]
        self.assertIn("R13 Italian Grand Prix", as_of)
        self.assertIn("configured snapshot", as_of)


class StandingsCacheTests(unittest.TestCase):
    def _fixtures(self):
        from analyzer.standings import DriverStanding, TeamStanding

        drivers = [DriverStanding(name="Kimi Antonelli", team="Mercedes", standing=1, points=267)]
        teams = [TeamStanding(name="Mercedes", standing=1, points=468)]
        return drivers, teams

    def _config(self) -> dict:
        return {
            "season_context": {
                "standings_refresh": {
                    "enabled": True,
                    "cache_path": "output/standings_cache.json",
                    "cache_max_age_sec": 3600,
                },
                "races": [],
                "team_baseline": {"teams": [{"name": "Mercedes", "constructors_points": 0}]},
            }
        }

    def test_round_trip_and_reuse_within_the_window(self) -> None:
        from analyzer.standings import load_standings_cache, save_standings_cache

        drivers, teams = self._fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            save_standings_cache(root, self._config(), drivers, teams, now)

            fresh = load_standings_cache(root, self._config(), now + timedelta(minutes=5))
            stale = load_standings_cache(root, self._config(), now + timedelta(hours=2))

        self.assertIsNotNone(fresh)
        self.assertEqual(fresh[0][0].name, "Kimi Antonelli")
        self.assertEqual(fresh[1][0].points, 468)
        self.assertIsNone(stale, "a cache older than cache_max_age_sec must be ignored")

    def test_cache_is_disabled_when_max_age_is_zero(self) -> None:
        from analyzer.standings import load_standings_cache, save_standings_cache

        drivers, teams = self._fixtures()
        config = self._config()
        config["season_context"]["standings_refresh"]["cache_max_age_sec"] = 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            save_standings_cache(root, config, drivers, teams, now)

            self.assertIsNone(load_standings_cache(root, config, now))

    def test_corrupt_cache_is_ignored_not_fatal(self) -> None:
        from analyzer.standings import load_standings_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            (root / "output" / "standings_cache.json").write_text("{not json", encoding="utf-8")

            result = load_standings_cache(
                root, self._config(), datetime(2026, 9, 11, tzinfo=timezone.utc)
            )

        self.assertIsNone(result)

    def test_refresh_uses_cache_instead_of_fetching_again(self) -> None:
        from analyzer import standings as standings_module
        from analyzer.standings import save_standings_cache

        drivers, teams = self._fixtures()
        config = self._config()
        calls = {"n": 0}

        def fail_if_called(_url, _timeout):
            calls["n"] += 1
            raise AssertionError("should have used the cache")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            save_standings_cache(root, config, drivers, teams, now)

            original_d, original_t = (
                standings_module.fetch_driver_standings,
                standings_module.fetch_team_standings,
            )
            try:
                standings_module.fetch_driver_standings = fail_if_called
                standings_module.fetch_team_standings = fail_if_called
                refreshed = standings_module.refresh_team_baseline_from_standings(
                    config, now + timedelta(minutes=1), root=root, persist=True
                )
            finally:
                standings_module.fetch_driver_standings = original_d
                standings_module.fetch_team_standings = original_t

        self.assertTrue(refreshed)
        self.assertEqual(calls["n"], 0, "network must not be hit when the cache is fresh")
        self.assertEqual(config["season_context"]["team_baseline"]["teams"][0]["constructors_points"], 468)
