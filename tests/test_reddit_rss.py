from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from analyzer.normalize import dedupe_posts
from collectors.base import PostItem
from collectors.reddit import _collect_subreddit_rss

COMMENTS = "https://www.reddit.com/r/formula1/comments/{id}/slug/"


def _entry(entry_id: str, title: str, submitted: str | None) -> str:
    body = "&lt;!-- SC_OFF --&gt;&lt;div class=&quot;md&quot;&gt;&lt;p&gt;Self text&lt;/p&gt;&lt;/div&gt;"
    if submitted:
        body = ("&lt;table&gt; submitted by &lt;a href=&quot;https://www.reddit.com/user/u&quot;&gt; /u/u &lt;/a&gt; "
                f"&lt;span&gt;&lt;a href=&quot;{submitted}&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; "
                f"&lt;span&gt;&lt;a href=&quot;{COMMENTS.format(id=entry_id)}&quot;&gt;[comments]&lt;/a&gt;&lt;/span&gt;")
    return (f"<entry><title>{title}</title><link href=\"{COMMENTS.format(id=entry_id)}\" />"
            f"<updated>2026-10-07T03:00:00+00:00</updated><content type=\"html\">{body}</content></entry>")


FEED = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><feed xmlns=\"http://www.w3.org/2005/Atom\">"
        + _entry("a1", "F1 to return to Africa for brand new grand prix",
                 "https://racingnews365.com/breaking-f1-to-return-to-africa?utm_source=x&amp;amp;ref=y")
        + _entry("a2", "Alonso still believes", "https://i.redd.it/dyjt48ncaxth1.jpeg")
        + _entry("a3", "Daily Discussion Thread", None)
        + "</feed>").encode()


class RedditRssFallbackTests(unittest.TestCase):
    def _collect(self) -> list[PostItem]:
        response = SimpleNamespace(content=FEED, raise_for_status=lambda: None)
        with patch("collectors.reddit.requests.get", return_value=response):
            return _collect_subreddit_rss("formula1", 25)

    def test_link_post_uses_submitted_article_url(self) -> None:
        post = self._collect()[0]
        self.assertEqual(post.url, "https://racingnews365.com/breaking-f1-to-return-to-africa?utm_source=x&ref=y")
        self.assertEqual(post.extra["comments_url"], COMMENTS.format(id="a1"))
        self.assertEqual(post.source, "reddit")

    def test_media_post_keeps_media_url(self) -> None:
        self.assertEqual(self._collect()[1].url, "https://i.redd.it/dyjt48ncaxth1.jpeg")

    def test_self_post_without_link_keeps_comments_url(self) -> None:
        post = self._collect()[2]
        self.assertEqual(post.url, COMMENTS.format(id="a3"))
        self.assertEqual(post.extra["comments_url"], COMMENTS.format(id="a3"))


class DedupePreferenceTests(unittest.TestCase):
    def _post(self, source: str, likes: int = 0) -> PostItem:
        return PostItem(source=source, text=source, title=source, likes=likes,
                        url="https://www.motorsport.com/f1/news/aduo/123/",
                        created_at=datetime(2026, 10, 7, tzinfo=timezone.utc))

    def test_publisher_item_wins_tie_with_social_share(self) -> None:
        kept = dedupe_posts([self._post("reddit"), self._post("rss")])
        self.assertEqual([p.source for p in kept], ["rss"])

    def test_higher_engagement_social_post_still_wins(self) -> None:
        kept = dedupe_posts([self._post("rss"), self._post("reddit", likes=500)])
        self.assertEqual([p.source for p in kept], ["reddit"])


if __name__ == "__main__":
    unittest.main()
