import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indicators import (
    _sma_cn_seed,
    add_atr,
    add_boll,
    add_kdj,
    add_macd,
    add_rsi,
    add_volume,
    compute_all,
)


def _ohlc(close, volume=1.0):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": np.full(len(close), volume, dtype=float),
    })


class TestIndicators(unittest.TestCase):
    def test_macd_histogram_is_twice_dif_minus_dea(self):
        df = add_macd(_ohlc(np.linspace(10, 20, 80)))
        self.assertTrue(np.allclose(df["macd"], 2 * (df["dif"] - df["dea"]), equal_nan=True))

    def test_rsi_all_up_reaches_100(self):
        df = add_rsi(_ohlc(np.arange(1, 41, dtype=float)))
        self.assertAlmostEqual(df["rsi6"].iloc[-1], 100.0, places=6)
        self.assertAlmostEqual(df["rsi12"].iloc[-1], 100.0, places=6)

    def test_rsi_all_down_reaches_0(self):
        df = add_rsi(_ohlc(np.arange(40, 0, -1, dtype=float)))
        self.assertAlmostEqual(df["rsi6"].iloc[-1], 0.0, places=6)

    def test_rsi_flat_is_50(self):
        df = add_rsi(_ohlc(np.full(30, 10.0)))
        self.assertTrue(np.allclose(df["rsi6"].iloc[5:], 50.0))

    def test_boll_uses_population_std(self):
        close = np.concatenate([np.full(19, 10.0), [12.0]])
        df = add_boll(_ohlc(close), n=20, k=2)
        window = close[-20:]
        mid = window.mean()
        std0 = window.std(ddof=0)
        self.assertAlmostEqual(df["boll_mid"].iloc[-1], mid)
        self.assertAlmostEqual(df["boll_up"].iloc[-1], mid + 2 * std0)
        sample_up = mid + 2 * window.std(ddof=1)
        self.assertNotAlmostEqual(df["boll_up"].iloc[-1], sample_up)

    def test_kdj_matches_tongdaxin_sma_seed(self):
        df = _ohlc(np.linspace(10, 20, 30))
        out = add_kdj(df.copy(), n=9, m1=3, m2=3)
        low_n = df["low"].rolling(9).min()
        high_n = df["high"].rolling(9).max()
        rsv = (df["close"] - low_n) / (high_n - low_n) * 100
        k_ref = _sma_cn_seed(rsv, 3, seed=50.0)
        d_ref = _sma_cn_seed(k_ref, 3, seed=50.0)
        self.assertTrue(np.allclose(out["kdj_k"], k_ref, equal_nan=True))
        self.assertTrue(np.allclose(out["kdj_d"], d_ref, equal_nan=True))
        self.assertTrue(np.allclose(out["kdj_j"], 3 * k_ref - 2 * d_ref, equal_nan=True))
        self.assertTrue(np.isnan(out["kdj_k"].iloc[0]))
        first_valid = out["kdj_k"].first_valid_index()
        self.assertIsNotNone(first_valid)

    def test_sma_seed_first_valid_from_50(self):
        s = pd.Series([np.nan, np.nan, 100.0, 100.0])
        k = _sma_cn_seed(s, 3, seed=50.0)
        self.assertTrue(np.isnan(k.iloc[0]))
        self.assertAlmostEqual(k.iloc[2], (2 / 3) * 50 + (1 / 3) * 100)

    def test_volume_ratio_excludes_current_bar(self):
        df = add_volume(_ohlc(np.full(10, 10.0), volume=1.0))
        df.loc[df.index[-1], "volume"] = 2.0
        df = add_volume(df)
        # 基准为截至上一周期的均量(=1.0)，当日本身不参与基准
        self.assertAlmostEqual(df["vol_ratio"].iloc[-1], 2.0)
        self.assertTrue(np.isnan(df["vol_ratio"].iloc[0]))

    def test_atr_constant_range(self):
        # 每根K线 high-low 固定 0.4 且收盘单调上行，TR 恒为 0.4，ATR 应收敛于 0.4
        df = _ohlc(np.linspace(10, 20, 60))
        out = add_atr(df.copy(), n=14)
        self.assertAlmostEqual(out["atr"].iloc[-1], 0.4, places=6)
        self.assertFalse(np.isnan(out["atr"].iloc[-1]))

    def test_compute_all_weekly_skips_ma120(self):
        df = compute_all(_ohlc(np.linspace(10, 20, 80)), period="weekly")
        self.assertIn("ma60", df.columns)
        self.assertNotIn("ma120", df.columns)
        self.assertIn("vol_ratio", df.columns)

    def test_compute_all_daily_has_ma250(self):
        df = compute_all(_ohlc(np.linspace(10, 20, 260)), period="daily")
        self.assertIn("ma120", df.columns)
        self.assertIn("ma250", df.columns)
        self.assertFalse(np.isnan(df["ma250"].iloc[-1]))


if __name__ == "__main__":
    unittest.main()
