from __future__ import annotations

from difflib import SequenceMatcher

from collectors.base import PostItem

SOURCE_WEIGHTS = {
    "twitter": 1.2,
    "reddit": 1.0,
    "rss": 0.8,
}


def _title_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _cross_source_bonus(post: PostItem, all_posts: list[PostItem]) -> float:
    if not post.title:
        return 0.0
    matches = 0
    for other in all_posts:
        if other.url == post.url:
            continue
        if other.source == post.source:
            continue
        if _title_similarity(post.title, other.title) >= 0.7:
            matches += 1
    return matches * 15.0


def score_posts(posts: list[PostItem]) -> list[PostItem]:
    scored: list[PostItem] = []
    for post in posts:
        engagement = post.likes + post.replies * 2 + post.retweets * 3
        source_weight = SOURCE_WEIGHTS.get(post.source, 1.0)
        cross_bonus = _cross_source_bonus(post, posts)
        raw_score = engagement * source_weight + cross_bonus

        scored.append(
            PostItem(
                source=post.source,
                text=post.text,
                title=post.title,
                url=post.url,
                created_at=post.created_at,
                likes=post.likes,
                replies=post.replies,
                retweets=post.retweets,
                raw_score=raw_score,
                feed_name=post.feed_name,
                extra=post.extra,
            )
        )

    scored.sort(key=lambda p: p.raw_score, reverse=True)
    return scored
