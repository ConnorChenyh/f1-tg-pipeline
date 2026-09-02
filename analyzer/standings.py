from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

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


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
        standings.append(
            DriverStanding(
                name=name_candidates[0],
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
        standings.append(
            DriverStanding(
                name=_clean_text(match.group("name")),
                team=match.group("team"),
                standing=int(match.group("pos")),
                points=int(match.group("points")),
            )
        )
    return standings


def fetch_driver_standings(url: str = DRIVER_PAGE_URL, timeout_sec: int = 15) -> list[DriverStanding]:
    response = requests.get(
        url,
        timeout=timeout_sec,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
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


def fetch_team_standings(url: str = TEAM_PAGE_URL, timeout_sec: int = 15) -> list[TeamStanding]:
    response = requests.get(
        url,
        timeout=timeout_sec,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()
    standings = _extract_team_standings_from_tables(response.text)
    if not standings:
        standings = _extract_team_standings_from_text(response.text)
    if not standings:
        raise ValueError("no team standings parsed from Formula1 results page")
    return standings


def refresh_team_baseline_from_standings(
    config: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    season_cfg = config.get("season_context", {}) or {}
    standings_cfg = season_cfg.get("standings_refresh", {}) or {}
    if not standings_cfg.get("enabled", True):
        return False

    driver_url = standings_cfg.get("driver_url", DRIVER_PAGE_URL)
    team_url = standings_cfg.get("team_url", TEAM_PAGE_URL)
    timeout_sec = int(standings_cfg.get("timeout_sec", 15))
    try:
        driver_standings = fetch_driver_standings(str(driver_url), timeout_sec)
        team_standings = fetch_team_standings(str(team_url), timeout_sec)
    except Exception as exc:
        logger.warning("Standings refresh failed; using configured team_baseline snapshot: %s", exc)
        return False

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

    team_baseline["source"] = "live Formula1 driver and team standings"
    if now is not None:
        completed = [
            race
            for race in season_cfg.get("races", []) or []
            if race.get("end") and str(race["end"]) < now.date().isoformat()
        ]
        if completed:
            last_race = completed[-1]
            team_baseline["as_of"] = (
                f"live standings on {now.date().isoformat()}, after "
                f"R{last_race.get('round')} {last_race.get('name')}"
            )
        else:
            team_baseline["as_of"] = f"live standings on {now.date().isoformat()}"
    season_cfg["team_baseline"] = team_baseline
    config["season_context"] = season_cfg
    logger.info("Standings refresh succeeded from %s and %s", driver_url, team_url)
    return True
