import sys
import types
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAKE = types.ModuleType("akshare")


def _spot():
    return pd.DataFrame({
        "代码": ["600519", "000001", "300750"],
        "名称": ["贵州茅台", "平安银行", "宁德时代"],
        "最新价": [1500.0, 11.0, 200.0],
        "涨跌幅": [2.5, -1.0, 0.0],
        "成交额": [6.0e9, 2.0e9, 1.0e10],
    })


def _zt_pool(date=None):
    return pd.DataFrame({
        "名称": ["高标A", "高标B", "跟风C"],
        "连板数": [7, 3, 1],
    })


def _dt_pool(date=None):
    return pd.DataFrame({"名称": ["跌停X"]})


def _hsgt():
    return pd.DataFrame([
        {"板块": "沪股通", "今日资金净流入": -12.3},
        {"板块": "北向", "今日资金净流入": 55.6},
    ])


def _info(symbol=None):
    return pd.DataFrame([{"item": "行业", "value": "白酒"},
                         {"item": "总市值", "value": 1.8e12}])


def _boards():
    return pd.DataFrame([{"板块名称": "白酒", "涨跌幅": 1.8,
                          "上涨家数": 18, "下跌家数": 2}])


def _board_hist(symbol=None, start_date=None, end_date=None, period="daily", adjust=""):
    return pd.DataFrame({"收盘": [100.0, 101.0, 102.0, 104.0]})


def _cons(symbol=None):
    return pd.DataFrame({
        "名称": ["领涨股", "次强股", "茅台陪跑", "平庸股", "弱势股", "落后股"],
        "涨跌幅": [9.9, 5.2, 2.4, 0.3, -1.8, -3.1],
    })


def _news(symbol=None):
    return pd.DataFrame([
        {"发布时间": "2026-08-25 09:30:00", "文章来源": "证券时报",
         "新闻标题": "某酒企发布提价公告"},
    ])


def _lhb_stat(symbol="近三月"):
    return pd.DataFrame([{
        "代码": "600519", "名称": "贵州茅台", "上榜次数": 3,
        "龙虎榜净买额": 2.5e8, "机构买入次数": 4,
    }])


FAKE.stock_zh_a_spot_em = _spot
FAKE.stock_zt_pool_em = _zt_pool
FAKE.stock_zt_pool_dtgc_em = _dt_pool
FAKE.stock_hsgt_fund_flow_summary_em = _hsgt
FAKE.stock_individual_info_em = _info
FAKE.stock_board_industry_name_em = _boards
FAKE.stock_board_industry_hist_em = _board_hist
FAKE.stock_board_industry_cons_em = _cons
FAKE.stock_news_em = _news
FAKE.stock_lhb_stock_statistic_em = _lhb_stat


class TestMarketContext(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("akshare")
        sys.modules["akshare"] = FAKE
        import market_context as mc
        self.mc = mc
        mc._web_search = lambda name: ["- 《mock检索》测试快照"]

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("akshare", None)
        else:
            sys.modules["akshare"] = self._saved

    def test_context_covers_five_dimensions(self):
        text = self.mc.build_market_context("600519", "贵州茅台")
        for marker in ("市场情绪", "涨停", "所属行业板块", "北向资金",
                       "龙虎榜", "个股最新新闻", "网页检索"):
            self.assertIn(marker, text)

    def test_breadth_numbers_and_rank(self):
        text = self.mc.build_market_context("600519", "贵州茅台")
        self.assertIn("上涨 1 家 / 下跌 1 家", text)
        # 茅台成交额60亿，宁时代100亿 → 排名第2
        self.assertIn("第2 位", text)
        self.assertIn("最高连板 7 板", text)
        self.assertIn("高标A(7板)", text)
        self.assertIn("北向", text)

    def test_sector_block(self):
        text = self.mc.build_market_context("600519", "贵州茅台")
        self.assertIn("所属行业板块：白酒，当日涨跌幅 1.80%", text)
        self.assertIn("近3个交易日板块累计", text)
        self.assertIn("板块领涨：领涨股9.90%、次强股5.20%、茅台陪跑2.40%", text)
        self.assertIn("板块领跌：弱势股-1.80%、落后股-3.10%", text)

    def test_lhb_and_news(self):
        text = self.mc.build_market_context("600519", "贵州茅台")
        self.assertIn("近三月龙虎榜", text)
        self.assertIn("上榜次数:3", text)
        self.assertIn("某酒企发布提价公告", text)

    def test_graceful_failure_marks_gap(self):
        def boom(*a, **k):
            raise RuntimeError("net down")

        FAKE_copy = FAKE.stock_zh_a_spot_em
        FAKE.stock_zh_a_spot_em = boom
        try:
            text = self.mc.build_market_context("600519", "贵州茅台")
        finally:
            FAKE.stock_zh_a_spot_em = FAKE_copy
        self.assertIn("获取失败", text)          # 单节降级…
        self.assertIn("龙虎榜机构统计", text)     # …其余节照常输出

    def test_placeholder_passthrough(self):
        from ai_context import build_placeholders
        ph = build_placeholders(pd.DataFrame({"close": [1.0]}),
                                {"text": "t"}, market_ctx="CTXDATA")
        self.assertEqual(ph["market_ctx"], "CTXDATA")
        ph_empty = build_placeholders(pd.DataFrame({"close": [1.0]}), {"text": ""})
        self.assertIn("未采集", ph_empty["market_ctx"])


if __name__ == "__main__":
    unittest.main()
