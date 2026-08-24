import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indicators import compute_all
from report import build_report
from signals import (
    BEAR,
    BULL,
    NEUTRAL,
    boll_signals,
    kdj_signals,
    ma_signals,
    macd_signals,
    score,
)


def _frame(**cols):
    n = len(next(iter(cols.values())))
    data = {"date": pd.date_range("2024-01-01", periods=n, freq="B")}
    data.update(cols)
    return pd.DataFrame(data)


class TestSignals(unittest.TestCase):
    def test_score_one_vote_per_group(self):
        groups = [
            {"items": [("a", BULL), ("b", BULL), ("c", BEAR)]},
            {"items": [("a", BEAR), ("b", BEAR)]},
            {"items": [("a", BULL), ("b", BEAR)]},
            {"items": [("a", NEUTRAL)]},
        ]
        s = score(groups)
        self.assertEqual(s["bull"], 1)
        self.assertEqual(s["bear"], 1)
        self.assertEqual(s["total"], 2)
        self.assertAlmostEqual(s["ratio"], 0.5)

    def test_score_requires_min_votes(self):
        s = score([{"items": [("a", BULL)]}])
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["tone"], NEUTRAL)
        s0 = score([{"items": [("a", NEUTRAL)]}])
        self.assertEqual(s0["tone"], NEUTRAL)

    def test_ma_nan_is_insufficient_not_choppy(self):
        n = 25
        close = np.linspace(10, 12, n)
        df = compute_all(pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": np.ones(n),
        }))
        self.assertTrue(np.isnan(df["ma60"].iloc[-1]))
        g = ma_signals(df)
        texts = [t for t, _ in g["items"]]
        self.assertTrue(any("样本不足" in t for t in texts))
        self.assertFalse(any("交织" in t for t in texts))

    def test_ma_position_is_neutral_not_break(self):
        n = 80
        close = np.linspace(10, 20, n)
        df = compute_all(pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": np.ones(n),
        }))
        g = ma_signals(df)
        texts = [t for t, s in g["items"] if s != NEUTRAL or "MA5" in t]
        self.assertTrue(any("运行于MA5" in t for t, _ in g["items"]))
        self.assertFalse(any(t == "收盘价跌破MA5" or t == "收盘价站上MA5" for t, _ in g["items"]))

    def test_macd_below_zero_does_not_say_bull_trend(self):
        n = 80
        close = np.linspace(20, 10, n)
        df = compute_all(pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": np.ones(n),
        }))
        g = macd_signals(df)
        joined = " ".join(t for t, _ in g["items"])
        self.assertNotIn("多头格局延续", joined)
        if "DIF>DEA" in joined or "DIF<DEA" in joined:
            self.assertTrue("零轴下" in joined or "零轴上" in joined or "金叉" in joined or "死叉" in joined)

    def test_volume_filters_golden_breakout(self):
        df = _frame(
            close=[9.0, 12.0],
            ma5=[10.0, 10.0],
            ma20=[11.0, 11.0],
            ma60=[12.0, 12.0],
            vol_ratio=[0.5, 0.5],
            is_limit_up=[False, False],
            is_limit_down=[False, False],
        )
        g = ma_signals(df)
        text, status = g["items"][0]
        self.assertIn("量能不足", text)
        self.assertEqual(status, NEUTRAL)

    def test_limit_up_blocks_buy(self):
        df = _frame(
            close=[9.0, 12.0],
            ma5=[10.0, 10.0],
            ma20=[11.0, 11.0],
            ma60=[12.0, 12.0],
            vol_ratio=[2.0, 2.0],
            is_limit_up=[False, True],
            is_limit_down=[False, False],
        )
        g = ma_signals(df)
        text, status = g["items"][0]
        self.assertIn("涨停", text)
        self.assertEqual(status, NEUTRAL)

    def test_kdj_insufficient(self):
        df = _frame(kdj_k=[np.nan, np.nan], kdj_d=[np.nan, np.nan], kdj_j=[np.nan, np.nan])
        g = kdj_signals(df)
        self.assertEqual(g["items"][0][1], NEUTRAL)
        self.assertIn("样本不足", g["items"][0][0])

    def test_boll_insufficient(self):
        df = _frame(
            close=[1.0, 2.0],
            boll_up=[np.nan, np.nan],
            boll_low=[np.nan, np.nan],
            boll_mid=[np.nan, np.nan],
        )
        g = boll_signals(df)
        self.assertIn("样本不足", g["items"][0][0])

    def test_report_turnover_keeps_percent(self):
        n = 80
        close = np.linspace(10, 12, n)
        df = compute_all(pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": np.ones(n),
            "turnover": np.full(n, 0.8),
        }))
        text = build_report(df, name="测试", symbol="600000.SH")["text"]
        self.assertIn("0.80%", text)
        self.assertNotIn("80.00%", text)
        self.assertIn("T+1", text)
        self.assertIn("减仓/回避", text)


if __name__ == "__main__":
    unittest.main()
