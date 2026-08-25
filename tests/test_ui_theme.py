import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui_theme as ut


class TestUiTheme(unittest.TestCase):
    def test_hero_contains_primary_and_secondary_info(self):
        pct = (1500.0 / 1463.0 - 1) * 100
        html = ut.hero("贵州茅台", "600519.SH", ["日线", "前复权"],
                       price=1500.0, chg_pct=pct, prev_price=1463.0,
                       ohlc={"今开": "1470.00", "最高": "1510.00"},
                       ts="2026-08-25 15:00", closed_only=False)
        self.assertIn("1500.00", html)          # 一级：现价
        self.assertIn("+37.00 (+2.53%)", html)  # 一级：涨跌
        self.assertIn('class="chg up"', html)
        self.assertIn("今开", html)              # 二级：OHLC
        self.assertIn("600519.SH", html)

    def test_hero_down_direction(self):
        html = ut.hero("X", "000001.SZ", [], 10.0, -1.2, 10.12,
                       {}, ts="t", closed_only=True)
        self.assertIn('class="chg down"', html)
        self.assertIn("收盘价", html)

    def test_verdict_banner_conf_and_tone(self):
        html = ut.verdict_banner("多方占优", "bull", 4, 1, ops="T+1提示")
        self.assertIn("多空组别比 4:1", html)
        self.assertIn("置信参考 80%", html)
        self.assertIn(ut.UP, html)
        self.assertIn("T+1提示", html)

    def test_kpi_and_sig_group(self):
        k = ut.kpi("换手率", "3.21%", "温和", cls="neg")
        self.assertIn("换手率", k)
        self.assertIn("neg", k)
        g = ut.sig_group("均线系统 MA", [("金叉", "bull"), ("死叉", "bear"),
                                        ("中性", None)])
        self.assertEqual(g.count('class="sig'), 3)
        self.assertIn("▲", g)
        self.assertIn("▼", g)
        self.assertIn("—", g)

    def test_risk_panel_levels(self):
        for lv, cls in (("高", "lv-high"), ("中", "lv-mid"), ("低", "lv-low")):
            html = ut.risk_panel(lv, ["依据一", "依据二"])
            self.assertIn(cls, html)
            self.assertIn("依据一", html)

    def test_global_css_tokens(self):
        css = ut.apply_global_css()
        for token in ("--up:", "--down:", "--accent:", "tabular-nums",
                      ".card:hover"):
            self.assertIn(token, css)


if __name__ == "__main__":
    unittest.main()
