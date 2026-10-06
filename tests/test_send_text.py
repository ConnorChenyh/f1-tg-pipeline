from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("send_text", ROOT / "scripts" / "send_text.py")
send_text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(send_text)


class SplitMessageTests(unittest.TestCase):
    def test_short_text_is_one_message(self) -> None:
        self.assertEqual(send_text.split_message("结论：无问题\n\n详情见报告", limit=100),
                         ["结论：无问题\n\n详情见报告"])

    def test_splits_on_paragraphs_without_losing_text(self) -> None:
        text = "\n\n".join(f"第{i}段" + "字" * 30 for i in range(6))
        chunks = send_text.split_message(text, limit=80)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 80 for chunk in chunks))
        self.assertEqual("\n\n".join(chunks), text)

    def test_long_paragraph_falls_back_to_lines_then_hard_cut(self) -> None:
        text = "甲" * 50 + "\n" + "乙" * 250
        chunks = send_text.split_message(text, limit=100)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), "甲" * 50 + "乙" * 250)


if __name__ == "__main__":
    unittest.main()
