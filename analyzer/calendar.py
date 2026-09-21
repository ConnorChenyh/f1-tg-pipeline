from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from analyzer.net import RetryPolicy, request_with_retry

logger = logging.getLogger(__name__)
MONTHS = {name: number for number, name in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
DATE_RANGE = re.compile(r"\b(\d{2})\s*(?:([A-Za-z]{3})\s*)?[-–]\s*(\d{2})\s+([A-Za-z]{3})\b")


def _validate_races(races: list[dict[str, Any]], year: int, count: int) -> None:
    if not races or len(races) != count:
        raise ValueError("empty or incomplete Formula1 calendar")
    if [race["round"] for race in races] != list(range(1, count + 1)):
        raise ValueError("duplicate or missing calendar rounds")
    previous_end = None
    for race in races:
        start, end = date.fromisoformat(race["start"]), date.fromisoformat(race["end"])
        if (not race["name"] or start.year != year or end.year != year or start > end
                or (previous_end and start <= previous_end)):
            raise ValueError("invalid calendar event or date order")
        previous_end = end


def parse_calendar(html: str, year: int) -> list[dict[str, Any]]:
    """Parse full race cards, excluding testing and the abbreviated up-next cards.

    The official event name is preserved: trackCountry is a race label and must
    not be mistaken for the host country when a Grand Prix relocates.
    """
    soup = BeautifulSoup(html, "html.parser")
    races = []
    advertised_rounds = set()
    for card in soup.select(f'a[href^="/en/racing/{year}/"]'):
        text = card.get_text(" ", strip=True)
        round_match = re.search(r"\bROUND\s+(\d+)\b", text)
        if not round_match:
            continue
        round_no = int(round_match[1])
        advertised_rounds.add(round_no)
        if "FORMULA 1 " not in text:
            continue
        metadata = json.loads(base64.b64decode(card["data-f1rd-a7s-context"]))
        name = metadata["raceName"]
        dates = DATE_RANGE.search(text)
        if not dates or not name.endswith(str(year)) or name not in text:
            raise ValueError("missing or inconsistent official calendar card")
        start_month, end_month = MONTHS[(dates[2] or dates[4]).lower()], MONTHS[dates[4].lower()]
        races.append({
            "round": round_no,
            "name": name,
            "circuit": metadata.get("trackName", ""),
            "start": date(year, start_month, int(dates[1])).isoformat(),
            "end": date(year, end_month, int(dates[3])).isoformat(),
            "url": "https://www.formula1.com" + card["href"],
        })
    races.sort(key=lambda race: race["round"])
    _validate_races(races, year, max(advertised_rounds, default=0))
    return races


def fetch_calendar(url: str, year: int, settings: dict[str, Any]) -> list[dict[str, Any]]:
    response = request_with_retry(
        lambda: requests.get(url, timeout=int(settings.get("timeout_sec", 15)),
                             headers={"User-Agent": "Mozilla/5.0"}),
        method="calendar", policy=RetryPolicy.from_config(settings.get("retry")),
    )
    response.raise_for_status()
    return parse_calendar(response.text, year)


def refresh_calendar(
    config: dict[str, Any], now: datetime, *, root: Path, persist: bool = True,
    year: int | None = None,
) -> bool:
    season = config.setdefault("season_context", {})
    if not season.get("enabled", True):
        return False
    settings = season.get("calendar_refresh", {}) or {}
    year = year or now.year
    reference = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    url = f"https://www.formula1.com/en/racing/{year}"
    path = root / settings.get("cache_path", "output/calendar_cache.json")
    # Calendar-derived claims from older configs cannot override live evidence.
    for key in ("notes", "cancelled_or_removed", "breaks"):
        season.pop(key, None)
    season["races"] = []
    season["calendar_status"] = "unavailable"
    season["calendar_source"] = url
    season.pop("calendar_fetched_at", None)
    cached = None
    age = float("inf")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        age = (reference - fetched_at).total_seconds()
        if payload["year"] == year and payload["source"] == url and age >= 0:
            _validate_races(payload["races"], year, payload["round_count"])
            cached = payload
    except (OSError, ValueError, KeyError, TypeError):
        pass

    fresh_age = float(settings.get("cache_max_age_sec", 3600))
    fallback_age = float(settings.get("fallback_max_age_sec", 604800))
    if cached is not None and fresh_age > 0 and age <= fresh_age:
        payload, status = cached, "cached"
    else:
        try:
            races = fetch_calendar(url, year, settings)
            _validate_races(races, year, len(races))
            payload = {"year": year, "source": url, "fetched_at": reference.isoformat(),
                       "round_count": len(races), "races": races}
            status = "live"
        except Exception as exc:
            logger.warning("Official calendar refresh failed: %s", exc)
            if cached is None or age > fallback_age:
                logger.warning("No usable official calendar; calendar assertions disabled")
                return False
            payload, status = cached, "stale"
        if status == "live" and persist:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
            except OSError as exc:
                logger.warning("Could not persist calendar cache: %s", exc)

    season["races"] = payload["races"]
    season["calendar_status"] = status
    season["calendar_fetched_at"] = payload["fetched_at"]
    logger.info("Official calendar: %s, %d rounds, fetched %s", status, len(payload["races"]), payload["fetched_at"])
    return status != "stale"
