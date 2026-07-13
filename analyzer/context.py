from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RunContext:
    generated_at: datetime
    window_hours: int
    f1_season: int

    @classmethod
    def now(cls, window_hours: int) -> RunContext:
        now = datetime.now(timezone.utc)
        return cls(
            generated_at=now,
            window_hours=window_hours,
            f1_season=now.year,
        )

    def to_prompt_block(self) -> str:
        local = self.generated_at.astimezone()
        return (
            f"- Current datetime (local): {local.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"- F1 season year: {self.f1_season}\n"
            f"- Evidence window: last {self.window_hours} hours\n"
            f"- Treat post timestamps as the freshness anchor; do not rely on stale training knowledge."
        )
