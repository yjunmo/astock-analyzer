import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indicators import compute_all
from signals import BEAR, BULL, NEUTRAL
from trade_plan import build_trade_plan


def _series_df(close, high_extra=0.15, low_extra=0.15):
    close = np.asarray(close, dtype=float)
    return compute_all(pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close,
        "high": close + high_extra,
        "low": close - low_extra,
        "close": close,
        "volume": np.full(len(close), 1e6),
    }))


def _is_rounded(px) -> bool:
    return abs(px * 100 - round(px * 100)) < 1e-6


class TestTradePlan(unittest.TestCase):
    def setUp(self):
        self.up = _series_df(np.linspace(10, 16, 80))
        self.down = _series_df(np.linspace(16, 10, 80))
        self.flat = _series_df(12.0 + np.sin(np.arange(80) / 4.0))

    def test_bullish_plan_levels_ordered_and_rounded(self):
        plan = build_trade_plan(self.up, BULL)
        self.assertIsNotNone(plan)
        close = plan["close"]
        self.assertLess(plan["buy_low"], close)
        self.assertLess(plan["buy_low"], plan["buy_high"])
        self.assertLess(plan["stop"], plan["buy_low"])
        self.assertGreater(plan["target2"], plan["target1"])
        self.assertGreater(plan["breakout"], close)
        for key in ("buy_low", "buy_high", "breakout", "target1", "target2", "stop"):
            self.assertTrue(_is_rounded(plan[key]), key)

    def test_bearish_plan_trim_zone_above_close(self):
        plan = build_trade_plan(self.down, BEAR)
        self.assertIsNotNone(plan)
        close = plan["close"]
        self.assertGreater(plan["trim_low"], close - 0.5)
        self.assertLess(plan["exit_line"], close)
        self.assertLess(plan["down_watch"], plan["exit_line"])

    def test_neutral_plan_has_both_sides(self):
        plan = build_trade_plan(self.flat, NEUTRAL)
        self.assertIsNotNone(plan)
        close = plan["close"]
        self.assertLess(plan["buy_high"], plan["trim_low"])
        self.assertGreater(plan["breakout"], close)
        self.assertLess(plan["exit_line"], close)

    def test_insufficient_data_returns_none(self):
        self.assertIsNone(build_trade_plan(self.up.head(20), BULL))
        self.assertIsNone(build_trade_plan(None, BULL))
        no_ind = self.up.drop(columns=["atr"])
        self.assertIsNone(build_trade_plan(no_ind, BULL))

    def test_cards_and_note_present(self):
        plan = build_trade_plan(self.up, BULL)
        self.assertTrue(plan["cards"])
        for label, value in plan["cards"]:
            self.assertIsInstance(label, str)
            self.assertIsInstance(value, str)
        self.assertIn("不构成投资建议", plan["note"])


if __name__ == "__main__":
    unittest.main()
