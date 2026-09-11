from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Run directories produced by the pipeline look like 2026-09-11_120003.
RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")

# Shared state lives directly under output/ and must never be treated as a run.
SHARED_STATE_FILES = frozenset(
    {
        "topic_history.json",
        "story_memory.sqlite3",
        "story_memory.sqlite3-wal",
        "story_memory.sqlite3-shm",
        "season_context_state.json",
        "pending_telegram_deliveries.json",
        "pending_telegram_deliveries.json.tmp",
        "standings_cache.json",
        "active_run.json",
    }
)

NON_RUN_PREFIXES = ("bbc_",)


@dataclass
class ReferenceState:
    """Which run directories are referenced, and whether that is trustworthy.

    ``reliable`` is the important field: when a reference file exists but cannot
    be read or understood, the caller must not delete anything, because "we could
    not tell whether this run is referenced" is not the same as "it is not".
    """

    names: set[str] = field(default_factory=set)
    reliable: bool = True
    problems: list[str] = field(default_factory=list)

    def add(self, value: Any) -> None:
        if value:
            self.names.add(Path(str(value)).name)


def _is_run_dir(path: Path) -> bool:
    return path.is_dir() and bool(RUN_DIR_RE.match(path.name))


def _pending_queue_path(output_root: Path, root: Path, config: dict[str, Any]) -> Path:
    """Resolve the pending-delivery queue using the same setting as the publisher."""
    configured = (config.get("telegram", {}) or {}).get("pending_deliveries_path")
    if not configured:
        return output_root / "pending_telegram_deliveries.json"
    path = Path(str(configured))
    # The queue path is relative to the repository root, like every other
    # configured output path; keep an absolute path as given.
    if path.is_absolute():
        return path
    return root / path


def _referenced_runs(
    output_root: Path,
    config: dict[str, Any],
    root: Path,
) -> ReferenceState:
    """Runs that must survive pruning, plus whether we can trust the answer."""
    state = ReferenceState()

    pending_path = _pending_queue_path(output_root, root, config)
    if pending_path.exists():
        try:
            payload = json.loads(pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            state.reliable = False
            state.problems.append(f"{pending_path}: {type(exc).__name__}")
            payload = None
        if payload is not None:
            if (
                not isinstance(payload, dict)
                or "deliveries" not in payload
                or not isinstance(payload.get("deliveries"), list)
            ):
                state.reliable = False
                state.problems.append(f"{pending_path}: unexpected shape")
            else:
                for entry in payload["deliveries"]:
                    if not isinstance(entry, dict) or not entry.get("output_dir"):
                        state.reliable = False
                        state.problems.append(f"{pending_path}: malformed entry")
                        continue
                    state.add(entry["output_dir"])

    active_path = output_root / "active_run.json"
    if active_path.exists():
        try:
            payload = json.loads(active_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            state.reliable = False
            state.problems.append(f"{active_path}: {type(exc).__name__}")
            payload = None
        if payload is not None:
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("output_dir"), str)
                or not payload["output_dir"].strip()
            ):
                state.reliable = False
                state.problems.append(f"{active_path}: unexpected shape")
            else:
                state.add(payload["output_dir"])

    return state


def prune_output_runs(
    output_root: Path,
    config: dict[str, Any],
    now: datetime | None = None,
    root: Path | None = None,
) -> list[str]:
    """Delete old run directories and return their names.

    Only timestamp-named directories are candidates, so hand-made experiment
    folders are never touched. If the reference state cannot be established with
    confidence, nothing is deleted at all.
    """
    retention_cfg = config.get("output_retention", {}) or {}
    if not retention_cfg.get("enabled", True):
        return []

    keep_days = int(retention_cfg.get("keep_days", 14))
    keep_min = int(retention_cfg.get("keep_min_runs", 7))
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days)

    if not output_root.exists():
        return []

    candidates = [path for path in output_root.iterdir() if _is_run_dir(path)]
    candidates.sort(key=lambda path: path.name, reverse=True)

    if root is None:
        # Default: output/ sits directly under the repository root.
        root = output_root.parent

    references = _referenced_runs(output_root, config, root)
    if not references.reliable:
        logger.warning(
            "Retention skipped: reference state could not be established (%s); "
            "keeping every run directory",
            "; ".join(references.problems),
        )
        return []

    protected = set(references.names)
    protected |= {path.name for path in candidates[:keep_min]}

    removed: list[str] = []
    for path in candidates:
        if path.name in protected:
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError as exc:
            logger.warning("Retention: cannot stat %s: %s", path, exc)
            continue
        if modified >= cutoff:
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            logger.warning("Retention: cannot remove %s: %s", path, exc)
            continue
        removed.append(path.name)

    if removed:
        logger.info(
            "Retention: removed %d run directory(ies) older than %d days (kept %d referenced/recent)",
            len(removed),
            keep_days,
            len(protected),
        )
    return removed
