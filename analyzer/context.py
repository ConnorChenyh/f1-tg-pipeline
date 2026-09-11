from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RunContext:
    generated_at: datetime
    window_hours: int
    f1_season: int
    season_context: str = ""

    @classmethod
    def now(cls, window_hours: int) -> RunContext:
        now = datetime.now(timezone.utc)
        return cls(
            generated_at=now,
            window_hours=window_hours,
            f1_season=now.year,
        )

    def at(self, generated_at: datetime, window_hours: int | None = None) -> RunContext:
        """Rebuild the context around a specific instant.

        Used by --resume so a continued run keeps the original run time and the
        time-dependent filters stay consistent with the first attempt.
        """
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        return RunContext(
            generated_at=generated_at,
            window_hours=self.window_hours if window_hours is None else window_hours,
            f1_season=generated_at.year,
            season_context=self.season_context,
        )

    def with_season_context(self, season_context: str) -> RunContext:
        return RunContext(
            generated_at=self.generated_at,
            window_hours=self.window_hours,
            f1_season=self.f1_season,
            season_context=season_context,
        )

    def to_prompt_block(self) -> str:
        local = self.generated_at.astimezone()
        block = (
            f"- Current datetime (local): {local.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"- F1 season year: {self.f1_season}\n"
            f"- Evidence window: last {self.window_hours} hours\n"
            f"- Treat post timestamps as the freshness anchor; do not rely on stale training knowledge."
        )
        if self.season_context:
            block = f"{block}\n\n{self.season_context}"
        return block
