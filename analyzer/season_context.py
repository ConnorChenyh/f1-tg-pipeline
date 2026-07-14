from __future__ import annotations

from datetime import date, datetime
from typing import Any


def _clean_sentence(value: Any) -> str:
    return str(value).strip().rstrip(".")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _race_status(race: dict[str, Any], today: date) -> str:
    start = _parse_date(str(race["start"]))
    end = _parse_date(str(race["end"]))
    if today < start:
        return "scheduled"
    if start <= today <= end:
        return "current"
    return "completed"


def _format_race(race: dict[str, Any]) -> str:
    round_no = race.get("round")
    name = race.get("name")
    start = race.get("start")
    end = race.get("end")
    circuit = race.get("circuit")
    sprint = " Sprint" if race.get("sprint") else ""
    return f"R{round_no} {name} ({start} to {end}, {circuit}{sprint})"


def build_season_context_prompt(config: dict[str, Any], now: datetime) -> str:
    season_cfg = config.get("season_context", {}) or {}
    if not season_cfg.get("enabled", True):
        return ""

    races = season_cfg.get("races", []) or []
    if not races:
        return ""

    today = now.date()
    completed = [race for race in races if _race_status(race, today) == "completed"]
    current = [race for race in races if _race_status(race, today) == "current"]
    upcoming = [race for race in races if _race_status(race, today) == "scheduled"]
    next_race = current[0] if current else (upcoming[0] if upcoming else None)

    lines = [
        "Season calendar context:",
        f"- Current official season calendar has {len(races)} scheduled Grands Prix in this config.",
        f"- As of {today.isoformat()}, {len(completed)} Grands Prix have been completed.",
    ]
    if next_race:
        status = "current race weekend" if current else "next race"
        lines.append(f"- The {status} is {_format_race(next_race)}.")

    recent = completed[-3:]
    if recent:
        lines.append("- Most recent completed rounds: " + "; ".join(_format_race(race) for race in recent) + ".")

    future = upcoming[:4]
    if future:
        lines.append("- Upcoming rounds: " + "; ".join(_format_race(race) for race in future) + ".")

    notes = season_cfg.get("notes", []) or []
    if notes:
        lines.append("- Calendar notes: " + " ".join(str(note) for note in notes))

    cancelled = season_cfg.get("cancelled_or_removed", []) or []
    if cancelled:
        items = []
        for item in cancelled:
            if isinstance(item, dict):
                name = item.get("name")
                reason = item.get("reason")
                items.append(f"{name} ({reason})" if reason else str(name))
            else:
                items.append(str(item))
        lines.append("- Cancelled/removed from the current calendar: " + "; ".join(items) + ".")

    team_baseline = season_cfg.get("team_baseline", {}) or {}
    team_items = team_baseline.get("teams", []) or []
    if team_items:
        as_of = team_baseline.get("as_of")
        header = "- Current big-four car and performance baseline"
        if as_of:
            header += f" ({as_of})"
        lines.append(header + ":")
        for team in team_items:
            name = team.get("name")
            chassis = team.get("chassis")
            points = team.get("constructors_points")
            standing = team.get("constructors_position")
            detail_parts = []
            if chassis:
                detail_parts.append(f"chassis {chassis}")
            if standing and points is not None:
                detail_parts.append(f"P{standing} in Constructors with {points} points")
            elif standing:
                detail_parts.append(f"P{standing} in Constructors")
            elif points is not None:
                detail_parts.append(f"{points} Constructors points")
            for key in ("performance", "technical", "caveat"):
                value = team.get(key)
                if value:
                    detail_parts.append(_clean_sentence(value))
            if name and detail_parts:
                lines.append(f"  - {name}: " + "; ".join(detail_parts) + ".")

    lines.append(
        "- Use this calendar and team baseline only as temporal background. If evidence conflicts with it, state the conflict instead of silently rewriting facts."
    )
    return "\n".join(lines)
