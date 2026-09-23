"""Reader-facing copy regressions based on the user-provided screenshot."""
import unittest

from generator.quality_guard import blocking_issues, validate_digest


class ReaderFacingStyleTests(unittest.TestCase):
    def draft(self, content):
        return {"title": "围场新闻", "items": [{"headline": "Ugo Ugochukwu 拿下 FIA Formula 3 年度冠军", "content": content}]}

    def test_screenshot_editorial_process_is_blocked(self):
        content = (
            "ESPN 报道，已加冕 FIA Formula 3 年度冠军的 Ugo Ugochukwu 表示，"
            "他很自豪能在国际赛场上代表自己的尼日利亚血统，尽管他持美国国旗参赛。"
            "该报道正文中可直接引用的内容仅此一句，未披露他夺冠的分站、积分或赛季战绩，因此这里不作补充。"
            "需要区分的是，这里说的是 FIA Formula 3 的年度冠军头衔，与 F1 分站冠军或 F1 世界冠军不是同一个概念。"
        )
        codes = {issue.code for issue in blocking_issues(validate_digest(self.draft(content)))}
        self.assertIn("editorial_process_in_copy", codes)

    def test_process_disclaimers_are_blocked(self):
        for text in ("报道只讲了他的感受，没有提及夺冠分站，因此不作补充。",
                     "原文没有提供更多细节，这里不作推测。",
                     "目前证据不足，无法补充更多背景。",
                     "这则报道不一定属实，请读者自行核实。"):
            with self.subTest(text=text):
                self.assertIn("editorial_process_in_copy", {
                    issue.code for issue in blocking_issues(validate_digest(self.draft(text)))})

    def test_material_status_and_source_attribution_remain_allowed(self):
        for text in ("ESPN 报道，Ugochukwu 表示，他为自己的尼日利亚血统感到自豪。",
                     "相关规则仍待 FIA 世界汽车运动理事会批准。",
                     "据 Autosport 报道，双方正在讨论续约，尚未签署合同。",
                     "车队否认了有关退出比赛的传闻。",
                     "车队尚未公布替补车手。",
                     "FIA 表示，现有证据不足以认定车手违规。"):
            with self.subTest(text=text):
                self.assertNotIn("editorial_process_in_copy", {
                    issue.code for issue in validate_digest(self.draft(text))})

    def test_internal_risk_notes_are_not_reader_copy(self):
        draft = self.draft("Ugochukwu 谈到自己的尼日利亚血统。")
        draft["risk_note"] = "证据不足，因此不作补充。"
        self.assertNotIn("editorial_process_in_copy", {issue.code for issue in validate_digest(draft)})

    def test_short_complete_news_is_not_forced_to_expand(self):
        draft = self.draft("Ugochukwu 表示，他为自己的尼日利亚血统感到自豪。")
        topics = [{"evidence_posts": [{"fetch_status": "ok", "article_content": "Article " * 300}]}]
        issues = validate_digest(draft, topics, min_item_chars=430)
        self.assertNotIn("too_short_rich_evidence_item", {issue.code for issue in issues})


class ReaderStylePipelineTests(unittest.TestCase):
    def test_review_receives_style_issue_and_keeps_caveat_in_notes_only(self):
        from copy import deepcopy
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from analyzer.context import RunContext
        from generator.digest_writer import generate_digest

        source = "https://example.com/ugo"
        news = "ESPN 报道，Ugochukwu 表示，他为自己的尼日利亚血统感到自豪。"
        original = {"items": [{"ordinal": "一", "headline": "Ugochukwu 谈自己的尼日利亚血统",
                              "content": news + "因此这里不作补充。"}], "sources": [source]}
        checked = deepcopy(original)
        checked["items"][0]["content"] = news
        checked["fact_check_notes"] = ["原文没有更多赛季数据，因此不作补充。"]
        prompts = []

        def chat_json(model, system, user, validator=None, stage="unknown"):
            prompts.append((stage, system, user))
            payload = deepcopy(original if stage == "digest" else checked)
            if stage == "final_review":
                payload.pop("fact_check_notes", None)
                payload["review_notes"] = []
            return validator(payload) if validator else payload

        client = SimpleNamespace(model_writer="test", chat_json=chat_json)
        topics = [{"id": "ugo", "title_zh": "Ugochukwu 谈身份认同", "evidence_urls": [source],
                   "evidence_posts": [{"url": source, "source": "rss", "article_content": news,
                                       "fetch_status": "ok"}]}]
        draft, notes, codes = generate_digest(client, topics,
            RunContext(datetime(2026, 9, 23, tzinfo=timezone.utc), 24, 2026), "围场新闻",
            min_items=1, max_items=1, item_min_chars=999, item_target_chars=1234, item_max_chars=500)
        self.assertEqual(codes, [])
        self.assertEqual(draft["items"][0]["content"], news)
        self.assertTrue(any("不作补充" in note for note in notes))
        self.assertIn("editorial_process_in_copy", prompts[1][2])
        for stage, system, user in prompts:
            self.assertIn("没有最低字数", system)
            self.assertNotIn("999", user)
            self.assertNotIn("1234", user)


if __name__ == "__main__":
    unittest.main()
