#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.yaml"


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _parse_daily_at(value: str) -> dt_time:
    hour, minute = value.strip().split(":", 1)
    return dt_time(hour=int(hour), minute=int(minute))


def next_run_at(now: datetime, daily_at: dt_time) -> datetime:
    candidate = now.replace(
        hour=daily_at.hour,
        minute=daily_at.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def run_pipeline(config_path: Path, hours: int, push_telegram: bool) -> int:
    cmd = [
        sys.executable,
        str(ROOT / "run.py"),
        "--config",
        str(config_path),
        "--hours",
        str(hours),
    ]
    if push_telegram:
        cmd.append("--push-telegram")

    logging.info("Starting scheduled pipeline: %s", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=ROOT, check=False, env=os.environ.copy())
    logging.info("Scheduled pipeline exited with code %s", completed.returncode)
    return completed.returncode


def sleep_until(target: datetime) -> None:
    while True:
        now = datetime.now(target.tzinfo)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def main() -> int:
    setup_logging()
    load_dotenv(ROOT / ".env")

    config_path = Path(os.getenv("PIPELINE_CONFIG", str(DEFAULT_CONFIG)))
    config = load_config(config_path)
    scheduler_cfg = config.get("scheduler", {})

    timezone_name = os.getenv("SCHEDULE_TIMEZONE", scheduler_cfg.get("timezone", "Asia/Hong_Kong"))
    os.environ.setdefault("TZ", timezone_name)
    if hasattr(time, "tzset"):
        time.tzset()
    tz = ZoneInfo(timezone_name)
    daily_at = _parse_daily_at(os.getenv("SCHEDULE_DAILY_AT", scheduler_cfg.get("daily_at", "12:00")))
    hours = _env_int("SCHEDULE_HOURS", int(scheduler_cfg.get("hours", config.get("window_hours", 24))))
    push_telegram = _env_bool("SCHEDULE_PUSH_TELEGRAM", bool(scheduler_cfg.get("push_telegram", True)))
    run_on_start = _env_bool("SCHEDULE_RUN_ON_START", bool(scheduler_cfg.get("run_on_start", False)))

    logging.info(
        "Scheduler configured: daily_at=%s timezone=%s hours=%s push_telegram=%s run_on_start=%s",
        daily_at.strftime("%H:%M"),
        timezone_name,
        hours,
        push_telegram,
        run_on_start,
    )

    if run_on_start:
        run_pipeline(config_path, hours, push_telegram)

    while True:
        target = next_run_at(datetime.now(tz), daily_at)
        logging.info("Next scheduled run: %s", target.isoformat())
        sleep_until(target)
        run_pipeline(config_path, hours, push_telegram)


if __name__ == "__main__":
    sys.exit(main())
