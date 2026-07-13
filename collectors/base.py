from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PostItem:
    source: str
    text: str
    title: str | None
    url: str
    created_at: datetime
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    raw_score: float = 0.0
    feed_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostItem:
        created = data["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        return cls(
            source=data["source"],
            text=data["text"],
            title=data.get("title"),
            url=data["url"],
            created_at=created,
            likes=int(data.get("likes", 0)),
            replies=int(data.get("replies", 0)),
            retweets=int(data.get("retweets", 0)),
            raw_score=float(data.get("raw_score", 0.0)),
            feed_name=data.get("feed_name"),
            extra=data.get("extra", {}),
        )
