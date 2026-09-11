from __future__ import annotations

import logging
import re
import shutil
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

# Directories that are not pipeline runs (manual experiments, topic scripts).
NON_RUN_PREFIXES = ("bbc_",)


def _is_run_dir(path: Path) -> bool:
    return path.is_dir() and bool(RUN_DIR_RE.match(path.name))


def _referenced_runs(output_root: Path, config: dict[str, Any]) -> set[str]:
    """Run directory names that must survive pruning.

    A queued Telegram delivery or an unfinished resumable run still points at its
    output directory; deleting it would break compensation or resume.
    """
    references: set[str] = set()

    pending_path = output_root / "pending_telegram_deliveries.json"
    if pending_path.exists():
        try:
            import json

            payload = json.loads(pending_path.read_text(encoding="utf-8"))
            for entry in payload.get("deliveries", []) or []:
                if isinstance(entry, dict) and entry.get("output_dir"):
                    references.add(Path(str(entry["output_dir"])).name)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Retention: could not read %s: %s", pending_path, exc)

    active_path = output_root / "active_run.json"
    if active_path.exists():
        try:
            import json

            payload = json.loads(active_path.read_text(encoding="utf-8"))
            if payload.get("output_dir"):
                references.add(Path(str(payload["output_dir"])).name)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Retention: could not read %s: %s", active_path, exc)

    return references


def prune_output_runs(
    output_root: Path,
    config: dict[str, Any],
    now: datetime | None = None,
) -> list[str]:
    """Delete old run directories, keeping referenced ones, and return their names.

    Only directories matching the pipeline's timestamp pattern are considered, so
    hand-made experiment folders are left alone.
    """
    retention_cfg = (config.get("output_retention", {}) or {})
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

    protected = _referenced_runs(output_root, config)
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
