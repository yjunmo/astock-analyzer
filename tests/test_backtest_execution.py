import unittest

import numpy as np
import pandas as pd

from backtest import _run_loop


class TestBacktestExecution(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=3, freq="B"),
            "open": [10.0, 10.2, 10.4],
            "close": [10.1, 10.3, 10.5],
        })

    def test_penultimate_signal_executes_on_last_bar(self):
        buy = np.array([False, True, False])
        sell = np.zeros(3, dtype=bool)
        equity, trades = _run_loop(self.df, buy, sell, 0.0)
        self.assertEqual(len(trades), 1)
        self.assertTrue(trades[0]["open"])
        self.assertEqual(trades[0]["entry_date"], "2026-01-05")
        self.assertGreater(equity[-1], 1.0)

    def test_last_bar_signal_has_no_future_execution(self):
        buy = np.array([False, False, True])
        sell = np.zeros(3, dtype=bool)
        equity, trades = _run_loop(self.df, buy, sell, 0.0)
        self.assertEqual(trades, [])
        self.assertTrue(np.allclose(equity, 1.0))


if __name__ == "__main__":
    unittest.main()

