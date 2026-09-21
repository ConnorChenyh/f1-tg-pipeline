from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from analyzer.calendar import parse_calendar, refresh_calendar
from analyzer.season_context import build_season_context_prompt


HTML = (Path(__file__).parent / "fixtures/f1_calendar_2026.html").read_text()
NOW = datetime(2026, 9, 21, 4, tzinfo=timezone.utc)


class CalendarTests(unittest.TestCase):
    def test_official_calendar_preserves_relocated_event_and_cross_month_dates(self):
        races = parse_calendar(HTML, 2026)
        self.assertEqual(len(races), 23)
        self.assertEqual(races[15]["round"], 16)
        self.assertIn("BAHRAIN GRAND PRIX IN MALAYSIA", races[15]["name"])
        self.assertEqual(races[15]["circuit"], "Kuala Lumpur")
        self.assertEqual(races[15]["start"], "2026-10-02")
        self.assertEqual(races[15]["end"], "2026-10-04")
        self.assertEqual(races[18]["start"], "2026-10-30")
        self.assertEqual(races[18]["end"], "2026-11-01")

    def test_rejects_wrong_year_and_partial_or_broken_page(self):
        for html, year in [(HTML, 2027), ("<html>blocked</html>", 2026),
                           (HTML.replace("ROUND 8", "ROUND 7"), 2026),
                           (HTML.replace("02 - 04 Oct", "TBC"), 2026)]:
            with self.subTest(year=year, html=html[:20]):
                with self.assertRaises(ValueError):
                    parse_calendar(html, year)

    def test_round_count_and_dates_are_not_fixed_to_fixture(self):
        soup = BeautifulSoup(HTML, "html.parser")
        for card in soup.select('a[href]'):
            if "ROUND 23" in card.get_text(" ", strip=True):
                card.decompose()
        races = parse_calendar(str(soup).replace("02 - 04 Oct", "01 - 03 Oct"), 2026)
        self.assertEqual(len(races), 22)
        self.assertEqual(races[15]["start"], "2026-10-01")
        self.assertEqual(races[15]["end"], "2026-10-03")

    def test_unavailable_calendar_does_not_mean_season_complete(self):
        from analyzer.season_monitor import build_season_snapshot
        config = {"season_context": {"races": [], "calendar_status": "unavailable"}}
        snapshot = build_season_snapshot(config, NOW, standings_refreshed=False)
        self.assertEqual(snapshot["phase"], "unknown")

    def test_disabled_context_does_not_fetch(self):
        with patch("analyzer.calendar.fetch_calendar") as fetch:
            refresh_calendar({"season_context": {"enabled": False}}, NOW, root=Path("."))
        fetch.assert_not_called()

    def test_refresh_replaces_legacy_claims_before_prompt_generation(self):
        config = {"season_context": {"races": [{"name": "old"}],
                  "notes": ["Bahrain returning is speculative"],
                  "cancelled_or_removed": ["Bahrain"], "breaks": [{"name": "old"}]}}
        with tempfile.TemporaryDirectory() as tmp, patch("analyzer.calendar.fetch_calendar", return_value=parse_calendar(HTML, 2026)):
            self.assertTrue(refresh_calendar(config, NOW, root=Path(tmp)))
            prompt = build_season_context_prompt(config, NOW)
        self.assertIn("23 scheduled Grands Prix", prompt)
        self.assertIn("BAHRAIN GRAND PRIX IN MALAYSIA", prompt)
        self.assertNotIn("speculative", prompt)
        self.assertNotIn("Cancelled/removed", prompt)
        self.assertIn("https://www.formula1.com/en/racing/2026", prompt)

    def test_cache_fresh_stale_expired_and_wrong_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"season_context": {}}
            with patch("analyzer.calendar.fetch_calendar", return_value=parse_calendar(HTML, 2026)):
                refresh_calendar(config, NOW, root=root)
            for delta, expected, fetch_called in [(timedelta(minutes=30), "cached", False),
                                                   (timedelta(days=1), "stale", True),
                                                   (timedelta(days=8), "unavailable", True),
                                                   (timedelta(days=365), "unavailable", True)]:
                current = copy.deepcopy(config)
                with patch("analyzer.calendar.fetch_calendar", side_effect=RuntimeError("offline")) as fetch:
                    refresh_calendar(current, NOW + delta, root=root)
                self.assertEqual(fetch.called, fetch_called)
                self.assertEqual(current["season_context"]["calendar_status"], expected)
                prompt = build_season_context_prompt(current, NOW + delta)
                if expected in ("stale", "unavailable"):
                    self.assertIn("Do not use", prompt)
                if expected == "unavailable":
                    self.assertEqual(current["season_context"]["races"], [])
                    self.assertNotIn("after the final", prompt)

    def test_failed_refresh_does_not_overwrite_cache_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"season_context": {}}
            with patch("analyzer.calendar.fetch_calendar", return_value=parse_calendar(HTML, 2026)):
                refresh_calendar(config, NOW, root=root, persist=False)
                self.assertFalse((root / "output").exists())
                refresh_calendar(config, NOW, root=root)
            cache = root / "output/calendar_cache.json"
            before = cache.read_bytes()
            with patch("analyzer.calendar.fetch_calendar", side_effect=ValueError("bad page")):
                refresh_calendar(config, NOW + timedelta(days=1), root=root)
            self.assertEqual(cache.read_bytes(), before)
            payload = json.loads(before)
            payload["races"] = payload["races"][:2]
            cache.write_text(json.dumps(payload))
            with patch("analyzer.calendar.fetch_calendar", side_effect=ValueError("bad page")):
                refresh_calendar(config, NOW + timedelta(days=1), root=root)
            self.assertEqual(config["season_context"]["calendar_status"], "unavailable")
