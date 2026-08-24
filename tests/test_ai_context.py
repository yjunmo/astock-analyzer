import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_context import (BARS_TAIL, DISCLAIMER, bars_table, build_placeholders,
                        parse_frontmatter, plan_lines, render_prompt,
                        snapshot_text)
from indicators import compute_all
from report import build_report


def _df(n=120):
    close = np.linspace(10, 14, n)
    return compute_all(pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close, "high": close + 0.15, "low": close - 0.15,
        "close": close, "volume": np.full(n, 1e6),
    }))


SKILL = """---
name: 测试技能
temperature: 0.3
max_tokens: 1800
---
你是分析师。报告：
{report}
价位：
{plan}
快照：{snapshot}
行情：
{bars}
自定义 {unknown} 保留。
"""


class TestAiContext(unittest.TestCase):
    def setUp(self):
        self.df = _df()
        self.res = build_report(self.df, name="测试", symbol="600000.SH")

    def test_bars_table_shape_and_tail(self):
        table = bars_table(self.df)
        lines = table.splitlines()
        self.assertLessEqual(len(lines), BARS_TAIL + 1)
        self.assertEqual(lines[0].split("|")[0], "date")
        self.assertIn("pct_chg", lines[0])
        # 第二列数据不含未格式化浮点
        sample = lines[-1].split("|")
        self.assertEqual(len(sample), len(lines[0].split("|")))

    def test_bars_tail_override_saves_tokens(self):
        table = bars_table(self.df, tail=10)
        self.assertEqual(len(table.splitlines()), 11)
        ph_default = build_placeholders(self.df, self.res)
        ph_small = build_placeholders(self.df, self.res, bars_tail=10)
        self.assertLess(len(ph_small["bars"]), len(ph_default["bars"]))

    def test_build_placeholders_keys(self):
        ph = build_placeholders(self.df, self.res,
                                snapshot={"price": 10.5, "prev_close": 10.0,
                                          "open": 10.1, "high": 10.6,
                                          "low": 10.05, "time": "15:00"})
        for key in ("report", "plan", "bars", "snapshot"):
            self.assertIsInstance(ph[key], str)
            self.assertTrue(ph[key])
        self.assertIn("综合评估", ph["report"])
        self.assertIn("最新价10.50（+5.00%）", ph["snapshot"])

    def test_render_prompt_replaces_and_appends_disclaimer(self):
        ph = build_placeholders(self.df, self.res)
        out = render_prompt(SKILL, ph)
        self.assertNotIn("{report}", out)
        self.assertNotIn("{bars}", out)
        self.assertIn("{unknown}", out)          # 未知占位符保留
        self.assertTrue(out.endswith(DISCLAIMER))
        self.assertIn("不构成投资建议", out)

    def test_parse_frontmatter(self):
        meta, body = parse_frontmatter(SKILL)
        self.assertEqual(meta["name"], "测试技能")
        self.assertAlmostEqual(float(meta["temperature"]), 0.3)
        self.assertEqual(int(meta["max_tokens"]), 1800)
        self.assertTrue(body.startswith("你是分析师"))

    def test_parse_frontmatter_absent(self):
        meta, body = parse_frontmatter("直接正文")
        self.assertEqual(meta, {})
        self.assertEqual(body, "直接正文")

    def test_plan_lines_without_plan(self):
        self.assertEqual(plan_lines({"plan": None}), "无")

    def test_snapshot_empty(self):
        self.assertEqual(snapshot_text(None), "无实时快照")


if __name__ == "__main__":
    unittest.main()
