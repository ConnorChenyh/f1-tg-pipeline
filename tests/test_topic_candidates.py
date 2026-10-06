from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import run as run_module
from analyzer.context import RunContext as RealRunContext
from generator.deepseek_client import RunDeadlineExceeded
from tests.harness import RunHarness, _base_config, _stub_post, _stub_topic


def _topics(count: int) -> list[dict]:
    topics = []
    for index in range(count):
        topic = _stub_topic()
        url = f"https://www.motorsport.com/f1/news/story-{index}"
        topic.update(id=f"topic_{index:02d}", title_zh=f"独立新闻 {index} 号", heat_score=90 - index,
                     evidence_urls=[url])
        topic["evidence_posts"] = [dict(topic["evidence_posts"][0], url=url, title=f"Story {index}")]
        topics.append(topic)
    return topics


class TopicCandidateExtraTests(unittest.TestCase):
    def _run(self, digest_cfg: dict, extracted: list[dict]) -> tuple[dict, list[dict]]:
        harness = RunHarness()
        client = harness._fake_client(harness._scripts(), [])
        seen: dict = {}

        def stop_at_digest(_client, topics, *args, **kwargs):
            seen["digest_topics"] = topics
            raise RunDeadlineExceeded("stop after topic selection")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir(parents=True)
            config = _base_config(root)
            config["digest"].update(digest_cfg)
            now = datetime.now(timezone.utc)
            with patch.object(run_module, "ROOT", root), \
                 patch.object(run_module, "load_config", return_value=config), \
                 patch.object(run_module, "build_output_dir", return_value=root / "output" / "run1"), \
                 patch.object(run_module, "collect_reddit", return_value=[]), \
                 patch.object(run_module, "collect_rss", return_value=[_stub_post(now)]), \
                 patch.object(run_module, "collect_twitter", return_value=[]), \
                 patch.object(run_module, "RunContext") as ctx_cls, \
                 patch.object(run_module, "refresh_calendar", return_value=False), \
                 patch.object(run_module, "refresh_team_baseline_from_standings", return_value=False), \
                 patch.object(run_module, "build_season_context_prompt", return_value=""), \
                 patch.object(run_module, "build_season_snapshot", return_value={}), \
                 patch.object(run_module, "load_season_snapshot", return_value=None), \
                 patch.object(run_module, "build_season_update_message", return_value=None), \
                 patch.object(run_module, "DeepSeekClient", return_value=client), \
                 patch.object(run_module, "extract_topics", return_value=extracted) as extract, \
                 patch.object(run_module, "generate_digest", side_effect=stop_at_digest), \
                 patch.object(run_module.sys, "argv", ["run.py", "--hours", "24"]):
                ctx_cls.now.return_value = RealRunContext.now(24)
                run_module.main()
        return extract.call_args.kwargs, seen["digest_topics"]

    def test_extraction_requests_spare_candidates_but_digest_keeps_max_items(self) -> None:
        kwargs, digest_topics = self._run({"max_items": 5, "topic_candidate_extra": 2}, _topics(7))
        self.assertEqual(kwargs["max_topics"], 7)
        self.assertEqual([t["id"] for t in digest_topics], [f"topic_{i:02d}" for i in range(5)])

    def test_default_requests_exactly_max_items(self) -> None:
        kwargs, digest_topics = self._run({"max_items": 5}, _topics(5))
        self.assertEqual(kwargs["max_topics"], 5)
        self.assertEqual(len(digest_topics), 5)


if __name__ == "__main__":
    unittest.main()
