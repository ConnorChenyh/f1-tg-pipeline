from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from analyzer.topics import _coerce_heat_score, _validate_topics_payload, extract_topics
from generator.deepseek_client import DeepSeekClient, ResponseShapeError
from generator.digest_writer import _validate_digest_payload


class _FakeCompletions:
    """Returns queued payloads, recording every prompt it was given."""

    def __init__(self, contents: list[str]):
        self.contents = contents
        self.prompts: list[str] = []

    def create(self, **kwargs: Any) -> Any:
        self.prompts.append(kwargs["messages"][-1]["content"])
        content = self.contents.pop(0) if self.contents else "{}"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def _client(contents: list[str]) -> tuple[DeepSeekClient, _FakeCompletions]:
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
        client = DeepSeekClient({"deepseek": {"max_retries": 1, "force_json_object": False}})
    completions = _FakeCompletions(contents)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


class HeatScoreCoercionTests(unittest.TestCase):
    def test_accepts_plain_ints_and_floats(self) -> None:
        self.assertEqual(_coerce_heat_score(87), 87)
        self.assertEqual(_coerce_heat_score(87.6), 87)

    def test_extracts_digits_from_model_chatter(self) -> None:
        # The original crash: int("85分") raised ValueError outside the retry loop.
        self.assertEqual(_coerce_heat_score("85分"), 85)
        self.assertEqual(_coerce_heat_score("heat_score: 72"), 72)

    def test_clamps_and_defaults_non_numeric(self) -> None:
        self.assertEqual(_coerce_heat_score(140), 100)
        self.assertEqual(_coerce_heat_score(-5), 0)
        self.assertEqual(_coerce_heat_score("高"), 0)
        self.assertEqual(_coerce_heat_score(None), 0)
        self.assertEqual(_coerce_heat_score(True), 0)


class TopicsPayloadValidationTests(unittest.TestCase):
    def test_rejects_non_object(self) -> None:
        with self.assertRaises(ResponseShapeError):
            _validate_topics_payload(["not", "an", "object"])

    def test_rejects_missing_topics_array(self) -> None:
        with self.assertRaises(ResponseShapeError):
            _validate_topics_payload({})

    def test_rejects_empty_topics_array(self) -> None:
        with self.assertRaises(ResponseShapeError):
            _validate_topics_payload({"topics": []})

    def test_rejects_topic_without_title(self) -> None:
        with self.assertRaises(ResponseShapeError):
            _validate_topics_payload({"topics": [{"heat_score": 80}]})

    def test_normalises_valid_payload(self) -> None:
        cleaned = _validate_topics_payload(
            {
                "topics": [
                    {
                        "title_zh": "  维斯塔潘续约  ",
                        "heat_score": "91分",
                        "evidence_urls": ["https://a.example/1", "", None],
                    }
                ]
            }
        )

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["title_zh"], "维斯塔潘续约")
        self.assertEqual(cleaned[0]["heat_score"], 91)
        self.assertEqual(cleaned[0]["id"], "topic_01")
        self.assertEqual(cleaned[0]["evidence_urls"], ["https://a.example/1"])


class SchemaRepairTests(unittest.TestCase):
    def test_schema_failure_triggers_repair_prompt_not_blind_retry(self) -> None:
        client, completions = _client(['{"topics": []}', '{"topics": [{"title_zh": "有效", "heat_score": 80}]}'])

        def validator(payload: Any) -> Any:
            return _validate_topics_payload(payload)

        result = client.chat_json("model", "system", "ORIGINAL PROMPT", validator=validator)

        self.assertEqual(result[0]["title_zh"], "有效")
        self.assertEqual(len(completions.prompts), 2)
        self.assertEqual(completions.prompts[0], "ORIGINAL PROMPT")
        self.assertIn("格式校验", completions.prompts[1])
        self.assertIn("topics", completions.prompts[1])

    def test_schema_failure_raises_after_attempts_exhausted(self) -> None:
        client, completions = _client(['{"topics": []}', '{"topics": []}'])

        with self.assertRaises(RuntimeError) as ctx:
            client.chat_json("model", "system", "p", validator=_validate_topics_payload)

        self.assertEqual(len(completions.prompts), 2)
        self.assertIn("schema", str(ctx.exception))

    def test_transport_error_is_retried_but_caller_error_is_not(self) -> None:
        client, completions = _client(['{"ok": true}'])
        calls = {"n": 0}

        def boom(**kwargs: Any) -> Any:
            calls["n"] += 1
            raise ValueError("bad request: unknown field")

        client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))

        with self.assertRaises(RuntimeError):
            client.chat_json("model", "system", "p")

        # A 4xx-style caller error must not burn the retry budget.
        self.assertEqual(calls["n"], 1)
        self.assertEqual(completions.prompts, [])


class DigestPayloadValidationTests(unittest.TestCase):
    def test_rejects_non_object(self) -> None:
        with self.assertRaises(ResponseShapeError):
            _validate_digest_payload("nope")

    def test_rejects_empty_object(self) -> None:
        # The old code accepted {} (isinstance dict) and failed later in the guard.
        with self.assertRaises(ResponseShapeError):
            _validate_digest_payload({})

    def test_rejects_item_missing_content(self) -> None:
        with self.assertRaises(ResponseShapeError):
            _validate_digest_payload({"items": [{"ordinal": "一", "headline": "标题"}]})

    def test_accepts_valid_digest(self) -> None:
        payload = {"items": [{"ordinal": "一", "headline": "标题", "content": "正文"}]}
        self.assertIs(_validate_digest_payload(payload), payload)


class ExtractTopicsResilienceTests(unittest.TestCase):
    def test_extract_topics_survives_non_numeric_heat_score(self) -> None:
        client, _ = _client(
            ['{"topics": [{"title_zh": "话题", "heat_score": "很高", "publish_recommendation": "publish"}]}']
        )
        posts = [
            SimpleNamespace(
                source="rss",
                title="t",
                text="body",
                url="https://a.example/1",
                created_at=__import__("datetime").datetime(2026, 9, 11, tzinfo=__import__("datetime").timezone.utc),
                raw_score=1.0,
                likes=0,
                replies=0,
                retweets=0,
            )
        ]

        topics = extract_topics(
            client,
            posts,
            heat_threshold=55,
            run_context=SimpleNamespace(to_prompt_block=lambda: "ctx"),
            min_topics=1,
            max_topics=5,
        )

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["heat_score"], 0)


if __name__ == "__main__":
    unittest.main()
