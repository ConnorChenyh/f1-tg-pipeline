from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from analyzer.net import RetryPolicy, request_with_retry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriverStanding:
    name: str
    team: str
    standing: int
    points: int


@dataclass(frozen=True)
class TeamStanding:
    name: str
    standing: int
    points: int


DRIVER_PAGE_URL = "https://www.formula1.com/en/results/2026/drivers"
TEAM_PAGE_URL = "https://www.formula1.com/en/results/2026/team"
BIG_FOUR_TEAMS = ("Mercedes", "Ferrari", "McLaren", "Red Bull Racing")

# formula1.com renders a driver as "Kimi Antonelli ANT ITA" (name, 3-letter
# driver code, nationality). Both the table and the text parser pick up the
# trailing codes, so they are stripped from the name field.
_TRAILING_CODE_RE = re.compile(r"\s+[A-Z]{2,4}$")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


# formula1.com renders a driver as "Kimi Antonelli ANT ITA" (name, 3-letter
# driver code, nationality). Both the table and the text parser pick up the
# trailing codes, so they are stripped from the name field.
_TRAILING_CODE_RE = re.compile(r"\s+[A-Z]{2,4}$")


def _clean_driver_name(value: str) -> str:
    """Drop trailing driver/nationality codes such as 'ANT' or 'ITA'."""
    name = _clean_text(value)
    while True:
        stripped = _TRAILING_CODE_RE.sub("", name)
        if stripped == name:
            return name
        name = stripped.strip()


def _parse_points(value: str) -> int | None:
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group(0)) if match else None


def _extract_from_tables(html: str) -> list[DriverStanding]:
    soup = BeautifulSoup(html, "html.parser")
    standings: list[DriverStanding] = []
    for row in soup.select("tr"):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in row.select("th,td")]
        cells = [cell for cell in cells if cell]
        if len(cells) < 4:
            continue
        standing = _parse_points(cells[0])
        points = _parse_points(cells[-1])
        if standing is None or points is None:
            continue
        team = ""
        for team_name in BIG_FOUR_TEAMS:
            if any(team_name.lower() in cell.lower() for cell in cells):
                team = team_name
                break
        if not team:
            continue
        name_candidates = [
            cell
            for cell in cells[1:-1]
            if team.lower() not in cell.lower()
            and not re.fullmatch(r"[A-Z]{2,4}", cell)
            and not re.fullmatch(r"\d+", cell)
        ]
        if not name_candidates:
            continue
        cleaned_name = _clean_driver_name(name_candidates[0])
        if not cleaned_name:
            continue
        standings.append(
            DriverStanding(
                name=cleaned_name,
                team=team,
                standing=standing,
                points=points,
            )
        )
    return standings


def _extract_from_text(html: str) -> list[DriverStanding]:
    text = _clean_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    pattern = re.compile(
        r"(?P<pos>\d+)\s+(?P<name>[A-Z][A-Za-z'. -]+?)\s+"
        r"(?P<team>Mercedes|Ferrari|McLaren|Red Bull Racing)\s+(?P<points>\d+)\b"
    )
    standings = []
    for match in pattern.finditer(text):
        name = _clean_driver_name(match.group("name"))
        if not name:
            continue
        standings.append(
            DriverStanding(
                name=name,
                team=match.group("team"),
                standing=int(match.group("pos")),
                points=int(match.group("points")),
            )
        )
    return standings


def _standings_request(url: str, timeout_sec: int, policy: RetryPolicy | None = None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    return request_with_retry(
        lambda: requests.get(url, timeout=timeout_sec, headers=headers),
        method="standings",
        policy=policy,
    )


def fetch_driver_standings(
    url: str = DRIVER_PAGE_URL,
    timeout_sec: int = 15,
    policy: RetryPolicy | None = None,
) -> list[DriverStanding]:
    response = _standings_request(url, timeout_sec, policy)
    response.raise_for_status()
    standings = _extract_from_tables(response.text)
    if not standings:
        standings = _extract_from_text(response.text)
    if not standings:
        raise ValueError("no driver standings parsed from Formula1 results page")
    return standings


def _extract_team_standings_from_tables(html: str) -> list[TeamStanding]:
    soup = BeautifulSoup(html, "html.parser")
    standings = []
    for row in soup.select("tr"):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in row.select("th,td")]
        cells = [cell for cell in cells if cell]
        if len(cells) < 3:
            continue
        standing = _parse_points(cells[0])
        points = _parse_points(cells[-1])
        if standing is None or points is None:
            continue
        name = next(
            (team_name for team_name in BIG_FOUR_TEAMS if any(team_name.lower() in cell.lower() for cell in cells[1:-1])),
            None,
        )
        if name:
            standings.append(TeamStanding(name=name, standing=standing, points=points))
    return standings


def _extract_team_standings_from_text(html: str) -> list[TeamStanding]:
    text = _clean_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    pattern = re.compile(
        r"(?P<pos>\d+)\s+(?P<team>Mercedes|Ferrari|McLaren|Red Bull Racing)\s+(?P<points>\d+)\b"
    )
    return [
        TeamStanding(
            name=match.group("team"),
            standing=int(match.group("pos")),
            points=int(match.group("points")),
        )
        for match in pattern.finditer(text)
    ]


def fetch_team_standings(
    url: str = TEAM_PAGE_URL,
    timeout_sec: int = 15,
    policy: RetryPolicy | None = None,
) -> list[TeamStanding]:
    response = _standings_request(url, timeout_sec, policy)
    response.raise_for_status()
    standings = _extract_team_standings_from_tables(response.text)
    if not standings:
        standings = _extract_team_standings_from_text(response.text)
    if not standings:
        raise ValueError("no team standings parsed from Formula1 results page")
    return standings


STANDINGS_CACHE_FILENAME = "standings_cache.json"


def _season_phase_label(season_cfg: dict[str, Any], now: datetime | None) -> str:
    """Describe where the season currently stands, from the configured calendar.

    This replaces a hardcoded "after R11 ... summer break" string in config.yaml
    that silently went stale as the season advanced.
    """
    if now is None:
        return "current season"
    today = now.date().isoformat()
    completed = [
        race
        for race in season_cfg.get("races", []) or []
        if race.get("end") and str(race["end"]) < today
    ]
    if not completed:
        return f"before the first race, as of {today}"
    last_race = completed[-1]
    return f"after R{last_race.get('round')} {last_race.get('name')}, as of {today}"


def _standings_cache_path(root: Path, season_cfg: dict[str, Any]) -> Path:
    configured = (season_cfg.get("standings_refresh", {}) or {}).get(
        "cache_path", f"output/{STANDINGS_CACHE_FILENAME}"
    )
    path = Path(str(configured))
    return path if path.is_absolute() else root / path


def load_standings_cache(
    root: Path,
    config: dict[str, Any],
    now: datetime | None = None,
) -> tuple[list[DriverStanding], list[TeamStanding]] | None:
    """Read freshly fetched standings so reruns do not re-scrape the same minute."""
    season_cfg = config.get("season_context", {}) or {}
    refresh_cfg = season_cfg.get("standings_refresh", {}) or {}
    max_age_sec = float(refresh_cfg.get("cache_max_age_sec", 0) or 0)
    if max_age_sec <= 0 or now is None:
        return None

    path = _standings_cache_path(root, season_cfg)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(str(payload["fetched_at"]).replace("Z", "+00:00"))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.info("Ignoring unusable standings cache %s: %s", path, exc)
        return None

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    reference = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    age_sec = (reference - fetched_at).total_seconds()
    if age_sec < 0 or age_sec > max_age_sec:
        return None

    try:
        drivers = [
            DriverStanding(
                name=str(item["name"]),
                team=str(item["team"]),
                standing=int(item["standing"]),
                points=int(item["points"]),
            )
            for item in payload["drivers"]
        ]
        teams = [
            TeamStanding(name=str(item["name"]), standing=int(item["standing"]), points=int(item["points"]))
            for item in payload["teams"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        logger.info("Standings cache %s has an unexpected shape: %s", path, exc)
        return None

    if not drivers or not teams:
        return None
    logger.info("Using cached standings from %s (age %.0fs)", path, age_sec)
    return drivers, teams


def save_standings_cache(
    root: Path,
    config: dict[str, Any],
    drivers: list[DriverStanding],
    teams: list[TeamStanding],
    fetched_at: datetime,
) -> None:
    """Persist fetched standings so what the digest was grounded on is inspectable."""
    season_cfg = config.get("season_context", {}) or {}
    path = _standings_cache_path(root, season_cfg)
    payload = {
        "fetched_at": fetched_at.isoformat(),
        "drivers": [
            {"name": d.name, "team": d.team, "standing": d.standing, "points": d.points} for d in drivers
        ],
        "teams": [{"name": t.name, "standing": t.standing, "points": t.points} for t in teams],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write standings cache %s: %s", path, exc)


def refresh_team_baseline_from_standings(
    config: dict[str, Any],
    now: datetime | None = None,
    root: Path | None = None,
    persist: bool = True,
) -> bool:
    season_cfg = config.get("season_context", {}) or {}
    standings_cfg = season_cfg.get("standings_refresh", {}) or {}
    if not standings_cfg.get("enabled", True):
        return False

    driver_url = standings_cfg.get("driver_url", DRIVER_PAGE_URL)
    team_url = standings_cfg.get("team_url", TEAM_PAGE_URL)
    timeout_sec = int(standings_cfg.get("timeout_sec", 15))
    policy = RetryPolicy.from_config(standings_cfg.get("retry"))

    cached = load_standings_cache(root, config, now) if root is not None else None
    if cached is not None:
        driver_standings, team_standings = cached
        source = "live Formula1 driver and team standings (cached)"
    else:
        try:
            driver_standings = fetch_driver_standings(str(driver_url), timeout_sec, policy)
            team_standings = fetch_team_standings(str(team_url), timeout_sec, policy)
        except Exception as exc:
            logger.warning("Standings refresh failed; using configured team_baseline snapshot: %s", exc)
            # Still refresh the phase label so the prompt never claims a stale
            # round even when the numbers come from the configured snapshot.
            team_baseline = season_cfg.get("team_baseline", {}) or {}
            team_baseline["as_of"] = f"configured snapshot, {_season_phase_label(season_cfg, now)}"
            season_cfg["team_baseline"] = team_baseline
            config["season_context"] = season_cfg
            return False
        source = "live Formula1 driver and team standings"
        if root is not None and persist and now is not None:
            save_standings_cache(root, config, driver_standings, team_standings, now)

    by_team: dict[str, list[DriverStanding]] = {}
    for standing in driver_standings:
        by_team.setdefault(standing.team, []).append(standing)
    team_by_name = {standing.name: standing for standing in team_standings}

    team_baseline = season_cfg.get("team_baseline", {}) or {}
    teams = team_baseline.get("teams", []) or []
    for team in teams:
        name = team.get("name")
        team_drivers = sorted(by_team.get(str(name), []), key=lambda item: item.standing)
        if not team_drivers:
            continue
        team["drivers"] = [
            {"name": driver.name, "standing": driver.standing, "points": driver.points}
            for driver in team_drivers
        ]
        official_team_standing = team_by_name.get(str(name))
        if official_team_standing:
            team["constructors_points"] = official_team_standing.points
            team["constructors_position"] = official_team_standing.standing
            team.pop("performance", None)

    team_baseline["source"] = source
    # Always recomputed from the calendar; never carried over from config.yaml.
    team_baseline["as_of"] = f"{source}, {_season_phase_label(season_cfg, now)}"
    season_cfg["team_baseline"] = team_baseline
    config["season_context"] = season_cfg
    logger.info("Standings refresh succeeded from %s and %s", driver_url, team_url)
    return True
