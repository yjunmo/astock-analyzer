import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_fetcher import (
    add_limit_flags,
    drop_unclosed_bar,
    ensure_turnover_percent,
    is_st_name,
    limit_ratio,
    merge_raw_ohlc,
    round_cent,
    to_weekly,
)


class TestDataFetcher(unittest.TestCase):
    def test_limit_ratio_boards(self):
        self.assertEqual(limit_ratio("sh600519"), 0.10)
        self.assertEqual(limit_ratio("sz300750"), 0.20)
        self.assertEqual(limit_ratio("sz301000"), 0.20)
        self.assertEqual(limit_ratio("sh688981"), 0.20)
        self.assertEqual(limit_ratio("bj430047"), 0.30)
        # 主板 ST 已放宽至 10%，创业板/科创板 ST 不降档仍为 20%
        self.assertEqual(limit_ratio("sh600000", is_st=True), 0.10)
        self.assertEqual(limit_ratio("sz300750", is_st=True), 0.20)
        self.assertEqual(limit_ratio("sh688981", is_st=True), 0.20)

    def test_st_name(self):
        self.assertTrue(is_st_name("*ST康美"))
        self.assertTrue(is_st_name("ST康美"))
        self.assertFalse(is_st_name("贵州茅台"))

    def test_round_cent_half_up(self):
        self.assertAlmostEqual(round_cent(pd.Series([11.055])).iloc[0], 11.06)
        self.assertAlmostEqual(round_cent(pd.Series([7.986])).iloc[0], 7.99)
        self.assertAlmostEqual(round_cent(pd.Series([10.0 * 1.1])).iloc[0], 11.00)

    def test_limit_up_on_unadjusted(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [10.0, 11.0],
            "high": [10.2, 11.0],
            "low": [9.8, 11.0],
            "close": [10.0, 11.0],
            "raw_open": [10.0, 11.0],
            "raw_high": [10.2, 11.0],
            "raw_low": [9.8, 11.0],
            "raw_close": [10.0, 11.0],
        })
        out = add_limit_flags(df, "sh600000")
        self.assertFalse(bool(out["is_limit_up"].iloc[0]))
        self.assertTrue(bool(out["is_limit_up"].iloc[1]))
        self.assertFalse(bool(out["is_limit_down"].iloc[1]))

    def test_drop_unclosed_daily(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-08-21", "2026-08-24"]),
            "close": [10.0, 10.2],
        })
        dropped = drop_unclosed_bar(df, "daily", now=pd.Timestamp("2026-08-24 10:00"))
        self.assertEqual(len(dropped), 1)
        kept = drop_unclosed_bar(df, "daily", now=pd.Timestamp("2026-08-24 15:10"))
        self.assertEqual(len(kept), 2)

    def test_drop_unclosed_weekly(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-08-14", "2026-08-21"]),
            "close": [10.0, 10.2],
        })
        dropped = drop_unclosed_bar(df, "weekly", now=pd.Timestamp("2026-08-19 10:00"))
        self.assertEqual(len(dropped), 1)
        kept = drop_unclosed_bar(df, "weekly", now=pd.Timestamp("2026-08-22 10:00"))
        self.assertEqual(len(kept), 2)

    def test_merge_raw_ohlc(self):
        adj = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "close": [9.5, 9.6],
        })
        raw = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.0, 10.1],
        })
        out = merge_raw_ohlc(adj, raw)
        self.assertAlmostEqual(out["raw_close"].iloc[-1], 10.1)
        self.assertAlmostEqual(out["close"].iloc[-1], 9.6)

    def test_sina_turnover_ratio_to_percent(self):
        n = 30
        shares = 653_035_625.0
        vol = 85_345_147.0
        df = pd.DataFrame({
            "date": pd.date_range("2026-07-01", periods=n, freq="B"),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": vol,
            "outstanding_share": shares,
            "turnover": vol / shares,
        })
        out = ensure_turnover_percent(df)
        self.assertAlmostEqual(out["turnover"].iloc[-1], vol / shares * 100.0, places=4)
        self.assertNotIn("outstanding_share", out.columns)
        self.assertGreater(out["turnover"].iloc[-1], 10)

    def test_already_percent_turnover_unchanged(self):
        df = pd.DataFrame({
            "date": pd.date_range("2026-07-01", periods=40, freq="B"),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 1.0,
            "turnover": 2.5,
        })
        out = ensure_turnover_percent(df)
        self.assertAlmostEqual(out["turnover"].iloc[-1], 2.5)

    def test_to_weekly_keeps_limit_flag_of_last_day(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]),
            "open": [10, 10, 10, 10, 11],
            "high": [10, 10, 10, 10, 11],
            "low": [10, 10, 10, 10, 11],
            "close": [10, 10, 10, 10, 11],
            "volume": [1, 1, 1, 1, 1],
            "is_limit_up": [False, False, False, False, True],
            "is_limit_down": [False, False, False, False, False],
        })
        w = to_weekly(df)
        self.assertEqual(len(w), 1)
        self.assertTrue(bool(w["is_limit_up"].iloc[0]))
        self.assertEqual(w["volume"].iloc[0], 5)


if __name__ == "__main__":
    unittest.main()
