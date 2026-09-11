from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUN_STATE_FILENAME = "run_state.json"
# Points at the most recent resumable run so --resume does not have to guess.
ACTIVE_RUN_FILENAME = "active_run.json"

# Pipeline stages in execution order. A stage is recorded once its artifacts are
# on disk, which is what makes a later --resume safe to skip it.
STAGE_COLLECT = "collect"
STAGE_TOPICS = "topics"
STAGE_DIGEST = "digest"
STAGE_IMAGES = "images"
STAGE_DELIVERED = "delivered"
STAGE_ORDER = (STAGE_COLLECT, STAGE_TOPICS, STAGE_DIGEST, STAGE_IMAGES, STAGE_DELIVERED)

DEFAULT_MAX_RESUME_AGE_HOURS = 6.0


@dataclass
class RunState:
    run_id: str
    generated_at: str
    window_hours: int
    completed: list[str] = field(default_factory=list)

    def has(self, stage: str) -> bool:
        return stage in self.completed

    def mark(self, stage: str) -> None:
        if stage not in self.completed:
            self.completed.append(stage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "window_hours": self.window_hours,
            "completed": list(self.completed),
        }


def parse_generated_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def run_state_path(output_dir: Path) -> Path:
    return output_dir / RUN_STATE_FILENAME


def active_run_path(root: Path) -> Path:
    return root / "output" / ACTIVE_RUN_FILENAME


def save_run_state(output_dir: Path, state: RunState) -> None:
    path = run_state_path(output_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write run state %s: %s", path, exc)


def load_run_state(output_dir: Path) -> RunState | None:
    path = run_state_path(output_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RunState(
            run_id=str(payload["run_id"]),
            generated_at=str(payload["generated_at"]),
            window_hours=int(payload["window_hours"]),
            completed=[str(item) for item in payload.get("completed", [])],
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Ignoring unreadable run state %s: %s", path, exc)
        return None


def mark_active_run(root: Path, output_dir: Path) -> None:
    path = active_run_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "output_dir": output_dir.name,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not record the active run in %s: %s", path, exc)


def find_resumable_run(
    root: Path,
    now: datetime,
    max_age_hours: float = DEFAULT_MAX_RESUME_AGE_HOURS,
) -> tuple[Path, RunState] | None:
    """Return the latest unfinished run, or None when there is nothing to resume.

    A run is resumable when its directory still exists, it has not reached the
    delivered stage, and it is younger than ``max_age_hours``. Resuming a stale
    run would write a digest for a window that has already passed, so those are
    refused and a fresh run is started instead.
    """
    pointer = active_run_path(root)
    if not pointer.exists():
        logger.info("No active run recorded; nothing to resume")
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        output_dir = root / "output" / str(payload["output_dir"])
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        logger.info("Ignoring unreadable active run pointer %s: %s", pointer, exc)
        return None

    state = load_run_state(output_dir)
    if state is None:
        logger.info("Run %s has no usable state; nothing to resume", output_dir.name)
        return None
    if state.has(STAGE_DELIVERED):
        logger.info("Run %s already reached delivery; nothing to resume", output_dir.name)
        return None

    generated_at = parse_generated_at(state.generated_at)
    if generated_at is None:
        logger.info("Run %s has an unparseable timestamp; nothing to resume", output_dir.name)
        return None

    reference = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    age = reference - generated_at
    if age > timedelta(hours=max_age_hours):
        logger.warning(
            "Refusing to resume %s: it is %.1fh old (limit %.1fh)",
            output_dir.name,
            age.total_seconds() / 3600,
            max_age_hours,
        )
        return None
    if age < timedelta(0):
        logger.warning("Refusing to resume %s: its timestamp is in the future", output_dir.name)
        return None

    return output_dir, state
