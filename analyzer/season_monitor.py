from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _monitor_config(config: dict[str, Any]) -> dict[str, Any]:
    return (config.get("season_context", {}) or {}).get("monitor", {}) or {}


def monitor_enabled(config: dict[str, Any]) -> bool:
    season_cfg = config.get("season_context", {}) or {}
    return bool(season_cfg.get("enabled", True)) and bool(_monitor_config(config).get("enabled", True))


def monitor_state_path(root: Path, config: dict[str, Any]) -> Path:
    configured = _monitor_config(config).get("state_path", "output/season_context_state.json")
    path = Path(configured)
    return path if path.is_absolute() else root / path


def _parse_date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _race_summary(race: dict[str, Any] | None) -> dict[str, Any] | None:
    if not race:
        return None
    return {
        "round": race.get("round"),
        "name": race.get("name"),
        "start": race.get("start"),
        "end": race.get("end"),
    }


def _phase_key(
    season_cfg: dict[str, Any],
    today: date,
    completed: list[dict[str, Any]],
    current: list[dict[str, Any]],
    upcoming: list[dict[str, Any]],
) -> str:
    if current:
        return f"race_weekend:{current[0].get('round')}"

    for item in season_cfg.get("breaks", []) or []:
        if not isinstance(item, dict) or not item.get("start") or not item.get("end"):
            continue
        if _parse_date(item["start"]) <= today <= _parse_date(item["end"]):
            return f"break:{item.get('name') or 'calendar_break'}"

    if not upcoming:
        return "season_complete"
    days_to_next = (_parse_date(upcoming[0]["start"]) - today).days
    if 0 <= days_to_next <= 6:
        return f"race_build_up:{upcoming[0].get('round')}"
    if completed:
        return f"between_races:{completed[-1].get('round')}:{upcoming[0].get('round')}"
    return f"pre_season:{upcoming[0].get('round')}"


def build_season_snapshot(
    config: dict[str, Any],
    now: datetime,
    *,
    standings_refreshed: bool,
) -> dict[str, Any]:
    season_cfg = config.get("season_context", {}) or {}
    races = season_cfg.get("races", []) or []
    today = now.date()
    completed = [race for race in races if _parse_date(race["end"]) < today]
    current = [
        race
        for race in races
        if _parse_date(race["start"]) <= today <= _parse_date(race["end"])
    ]
    upcoming = [race for race in races if _parse_date(race["start"]) > today]

    teams = []
    for team in (season_cfg.get("team_baseline", {}) or {}).get("teams", []) or []:
        teams.append(
            {
                "name": team.get("name"),
                "position": team.get("constructors_position"),
                "points": team.get("constructors_points"),
                "drivers": [
                    {
                        "name": driver.get("name"),
                        "position": driver.get("standing"),
                        "points": driver.get("points"),
                    }
                    for driver in team.get("drivers", []) or []
                    if isinstance(driver, dict)
                ],
            }
        )

    next_race = current[0] if current else (upcoming[0] if upcoming else None)
    return {
        "recorded_at": now.isoformat(),
        "date": today.isoformat(),
        "phase": _phase_key(season_cfg, today, completed, current, upcoming),
        "completed_rounds": [race.get("round") for race in completed],
        "last_completed_race": _race_summary(completed[-1] if completed else None),
        "next_race": _race_summary(next_race),
        "standings_refreshed": standings_refreshed,
        "teams": teams,
    }


def load_season_snapshot(root: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    if not monitor_enabled(config):
        return None
    path = monitor_state_path(root, config)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_season_snapshot(root: Path, config: dict[str, Any], snapshot: dict[str, Any]) -> None:
    if not monitor_enabled(config):
        return
    path = monitor_state_path(root, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _format_race(race: dict[str, Any] | None) -> str:
    if not race:
        return "无"
    return f"R{race.get('round')} {race.get('name')}（{race.get('start')} 至 {race.get('end')}）"


def _format_phase(value: Any) -> str:
    phase = str(value or "")
    if phase.startswith("break:"):
        return f"休赛期（{phase.split(':', 1)[1]}）"
    if phase.startswith("race_weekend:"):
        return f"R{phase.rsplit(':', 1)[1]} 比赛周末"
    if phase.startswith("race_build_up:"):
        return f"R{phase.rsplit(':', 1)[1]} 赛前周"
    if phase.startswith("between_races:"):
        return "两站比赛之间"
    if phase.startswith("pre_season:"):
        return "季前阶段"
    if phase == "season_complete":
        return "赛季结束"
    return phase or "未知"


def _team_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old_teams = {team.get("name"): team for team in previous.get("teams", []) or []}
    lines = []
    for team in current.get("teams", []) or []:
        name = team.get("name")
        old = old_teams.get(name)
        if not old:
            continue
        old_position, new_position = old.get("position"), team.get("position")
        old_points, new_points = old.get("points"), team.get("points")
        if (old_position, old_points) != (new_position, new_points):
            lines.append(f"- {name}：P{old_position} {old_points}分 → P{new_position} {new_points}分")
    return lines


def _driver_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old_drivers = {
        driver.get("name"): driver
        for team in previous.get("teams", []) or []
        for driver in team.get("drivers", []) or []
    }
    lines = []
    for team in current.get("teams", []) or []:
        for driver in team.get("drivers", []) or []:
            name = driver.get("name")
            old = old_drivers.get(name)
            if not old:
                continue
            old_position, new_position = old.get("position"), driver.get("position")
            old_points, new_points = old.get("points"), driver.get("points")
            if (old_position, old_points) != (new_position, new_points):
                lines.append(f"- {name}：P{old_position} {old_points}分 → P{new_position} {new_points}分")
    return lines


def build_season_update_message(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> str | None:
    if previous is None:
        return None

    old_rounds = set(previous.get("completed_rounds", []) or [])
    new_rounds = [item for item in current.get("completed_rounds", []) or [] if item not in old_rounds]
    phase_changed = previous.get("phase") != current.get("phase")
    team_changes = _team_changes(previous, current)
    driver_changes = _driver_changes(previous, current)
    if not new_rounds and not phase_changed and not team_changes and not driver_changes:
        return None

    lines = ["🏁 F1 赛季背景已自动更新"]
    if phase_changed:
        lines.append(
            f"- 阶段：{_format_phase(previous.get('phase'))} → {_format_phase(current.get('phase'))}"
        )
    if new_rounds:
        lines.append(f"- 新完赛：{_format_race(current.get('last_completed_race'))}")
    lines.append(f"- 已完成：{len(current.get('completed_rounds', []) or [])} 站")
    lines.append(f"- 当前/下一站：{_format_race(current.get('next_race'))}")
    lines.extend(team_changes)
    lines.extend(driver_changes)
    if not current.get("standings_refreshed"):
        lines.append("- 注意：本次官方积分刷新失败，运行时暂用配置快照")
    return "\n".join(lines)
