import unittest

import numpy as np
import pandas as pd

from astock.analysis.analytics import build_snapshot, signal_alignment
from astock.analysis.indicators import compute_all


def sample_frame(n=320, daily_return=0.001):
    close = 100 * (1 + daily_return) ** np.arange(n)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close * 0.998,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.linspace(1_000_000, 2_000_000, n),
    })


class TestAnalytics(unittest.TestCase):
    def test_alignment_is_explainable_not_probability(self):
        out = signal_alignment({"bull": 3, "bear": 1})
        self.assertEqual(out["direction"], "偏多")
        self.assertEqual(out["coverage"], 4)
        self.assertIn("不是涨跌概率", out["note"])
        self.assertGreater(out["value"], 50)

    def test_snapshot_uptrend_and_returns(self):
        df = compute_all(sample_frame())
        snap = build_snapshot(df, {"score": {"bull": 4, "bear": 0}})
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["trend"], "上升趋势")
        self.assertGreater(snap["returns"][20], 0)
        self.assertGreaterEqual(snap["range_position"], 99)
        self.assertIsNotNone(snap["atr_pct"])

    def test_snapshot_empty_is_safe(self):
        self.assertFalse(build_snapshot(pd.DataFrame())["ok"])


if __name__ == "__main__":
    unittest.main()

