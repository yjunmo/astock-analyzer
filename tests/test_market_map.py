import json
import unittest
from unittest.mock import patch

import pandas as pd

from astock.market import market_map as mm


def sample_quotes():
    return mm.normalize_quotes(pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1400, "涨跌幅": 2,
         "成交额": 4e9, "总市值": 200e9, "流通市值": 190e9},
        {"代码": "000001", "名称": "平安银行", "最新价": 10, "涨跌幅": -1,
         "成交额": 1e9, "总市值": 100e9, "流通市值": 90e9},
        {"代码": "300750", "名称": "宁德时代", "最新价": 300, "涨跌幅": 0,
         "成交额": 2e9, "总市值": 100e9, "流通市值": 70e9},
        {"代码": "688001", "名称": "ST示例", "最新价": 20, "涨跌幅": 6,
         "成交额": 1e9, "总市值": 10e9, "流通市值": 5e9},
        {"代码": "920001", "名称": "北交示例", "最新价": 10, "涨跌幅": -2,
         "成交额": 0, "总市值": None, "流通市值": None},
    ]), {"600519": "食品饮料", "000001": "银行", "300750": "电力设备", "688001": "电子"})


class Response:
    def __init__(self, data=None, text=""):
        self.data = data
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class TestMarketMap(unittest.TestCase):
    def test_normalization_drops_invalid_quotes_and_never_guesses_industry(self):
        frame = pd.DataFrame([
            {"代码": "sz000001", "名称": "平安银行", "最新价": "11", "涨跌幅": "1"},
            {"代码": "600001", "名称": "无报价", "最新价": 0, "涨跌幅": 0},
            {"代码": "600002", "名称": "坏数据", "最新价": 12, "涨跌幅": "--"},
            {"代码": "600003", "名称": "无穷", "最新价": float("inf"), "涨跌幅": 2},
            {"代码": "900001", "名称": "非A股", "最新价": 10, "涨跌幅": 1},
        ])
        result = mm.normalize_quotes(frame)
        self.assertEqual(result["代码"].tolist(), ["000001"])
        self.assertEqual(result.iloc[0]["行业"], "未分类")
        self.assertTrue(pd.isna(result.iloc[0]["总市值"]))

    def test_breadth_and_turnover_use_filtered_universe(self):
        frame = mm.filter_quotes(sample_quotes(), exclude_st=True)
        stats = mm.market_stats(frame)
        self.assertEqual((stats["count"], stats["up"], stats["down"], stats["flat"]), (4, 1, 2, 1))
        self.assertEqual(stats["up_ratio"], 25)
        self.assertEqual(stats["median"], -.5)
        self.assertEqual(stats["amount"], 7e9)

    def test_filters_literal_query_market_industry_direction_and_cap(self):
        frame = sample_quotes()
        self.assertEqual(mm.filter_quotes(frame, market="创业板")["代码"].tolist(), ["300750"])
        self.assertEqual(mm.filter_quotes(frame, industries=["银行"])["代码"].tolist(), ["000001"])
        self.assertEqual(mm.filter_quotes(frame, query="茅台")["代码"].tolist(), ["600519"])
        self.assertEqual(mm.filter_quotes(frame, query="000001")["代码"].tolist(), ["000001"])
        self.assertTrue(mm.filter_quotes(frame, query=".*").empty)
        self.assertEqual(len(mm.filter_quotes(frame, direction="仅下跌")), 2)
        self.assertEqual(mm.filter_quotes(frame, min_cap=1500)["代码"].tolist(), ["600519"])

    def test_weights_and_missing_data_do_not_silently_change_area_metric(self):
        frame = sample_quotes()
        groups, missing = mm.map_payload(frame, "总市值")
        self.assertEqual(missing, 1)
        self.assertEqual(sum(group["weight"] for group in groups), 410e9)
        equal, missing = mm.map_payload(frame, "等权")
        self.assertEqual(missing, 0)
        self.assertEqual(sum(group["weight"] for group in equal), 5)
        payload = json.dumps(equal, allow_nan=False)
        self.assertIn('"cap": null', payload)
        self.assertEqual(mm.map_payload(frame, "成交额")[1], 1)

    def test_sector_returns_are_cap_weighted_and_zero_cap_falls_back_to_mean(self):
        frame = sample_quotes().iloc[:2].copy()
        frame["行业"] = "组合"
        self.assertAlmostEqual(mm.sector_stats(frame)[0]["change"], 1)
        frame["总市值"] = 0
        self.assertAlmostEqual(mm.sector_stats(frame)[0]["change"], .5)

    def test_fetch_handles_partial_pages_and_converts_cap_units(self):
        def get(url, params=None):
            if url.endswith("getHQNodeStockCount"):
                return Response(201)
            page = params["page"]
            if page == 2:
                raise OSError("offline")
            return Response([{"code": "600519" if page == 1 else "000001", "name": "测试",
                              "trade": "10.1", "changepercent": "1.2", "mktcap": "12345",
                              "nmc": "10000", "amount": "5000000"}])
        frame, meta = mm.fetch_quotes(get)
        self.assertEqual(meta["expected"], 201)
        self.assertEqual(meta["failed_pages"], 1)
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[0]["总市值"], 123450000)
        self.assertEqual(frame.iloc[0]["流通市值"], 100000000)
        self.assertIn("+08:00", meta["fetched_at"])

    def test_industry_pagination_accepts_both_eastmoney_payload_shapes(self):
        def get(url, params=None):
            if params["pn"] == 1:
                return Response({"data": {"total": 101, "diff": {"0": {"f12": "000001", "f100": "银行"}}}})
            return Response({"data": {"total": 101, "diff": [{"f12": "600519", "f100": "白酒"}]}})
        mapping, source = mm.fetch_industries(get)
        self.assertEqual(mapping, {"000001": "银行", "600519": "白酒"})
        self.assertEqual(source, "东方财富行业")

    def test_industry_failure_uses_sina_and_keeps_unknown_codes_unclassified(self):
        def get(url, params=None):
            if "eastmoney" in url:
                raise OSError("offline")
            if url.endswith("newSinaHy.php"):
                return Response(text='var sectors={"new_bank":"new_bank,银行,1"};')
            return Response([{"code": "000001"}])
        mapping, source = mm.fetch_industries(get)
        self.assertEqual(mapping, {"000001": "银行"})
        self.assertEqual(source, "新浪行业")

    def test_empty_universe_and_invalid_area(self):
        empty = mm.normalize_quotes(pd.DataFrame())
        self.assertEqual(mm.market_stats(empty)["count"], 0)
        self.assertEqual(mm.map_payload(empty), ([], 0))
        with self.assertRaises(ValueError):
            mm.map_payload(sample_quotes(), "不存在")


class TestMarketMapView(unittest.TestCase):
    def test_main_route_is_independent_and_selection_opens_research(self):
        from streamlit.testing.v1 import AppTest
        from astock.ui import state
        from astock.paths import PROJECT_ROOT
        from views import market_map_page as page

        meta = {"source": "测试行情", "expected": 5, "failed_pages": 0,
                "fetched_at": "2026-11-07T10:00:00+08:00"}
        with patch.object(page, "_load_quotes", return_value=(sample_quotes(), meta)), \
             patch.object(page, "_load_industries", return_value=({}, "测试分类")), \
             patch.object(state, "fetch_realtime_cached", return_value={}) as realtime, \
             patch.object(state, "ensure_df_all", return_value=(pd.DataFrame(), None)):
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
            app.session_state["code_field"] = "000001"
            app.session_state["active_query"] = ("sz000001", "daily", "qfq")
            app.run(timeout=15)
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["main_nav"], "market_map")
            realtime.assert_not_called()
            app.selectbox(key="cloud_pick").select("600519").run()
            next(button for button in app.button if button.label == "进入个股研究 ↗").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["main_nav"], "analysis")
            self.assertEqual(app.session_state["code_field"], "600519")
            realtime.assert_called_once_with("sh600519")

    def test_controls_and_empty_search_without_network(self):
        from streamlit.testing.v1 import AppTest
        from views import market_map_page as page

        meta = {"source": "测试行情", "expected": 5, "failed_pages": 0,
                "fetched_at": "2026-11-07T10:00:00+08:00"}
        with patch.object(page, "_load_quotes", return_value=(sample_quotes(), meta)), \
             patch.object(page, "_load_industries", return_value=({"600519": "食品饮料"}, "测试分类")):
            app = AppTest.from_string("from views.market_map_page import render_market_map\nrender_market_map()")
            app.run(timeout=10)
            self.assertFalse(app.exception)
            app.selectbox(key="cloud_area").select("等权").run()
            self.assertFalse(app.exception)
            app.selectbox(key="cloud_pick").select("600519").run()
            self.assertTrue(any(button.label == "进入个股研究 ↗" for button in app.button))
            app.text_input(key="cloud_search").set_value("不存在的公司").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("没有符合条件" in item.value for item in app.info))

    def test_failure_retains_previous_snapshot(self):
        from streamlit.testing.v1 import AppTest
        from views import market_map_page as page

        meta = {"source": "测试行情", "expected": 5, "failed_pages": 0,
                "fetched_at": "2026-11-07T10:00:00+08:00"}
        with patch.object(page, "_load_quotes", side_effect=OSError("offline")), \
             patch.object(page, "_load_industries", return_value=({}, "测试分类")):
            app = AppTest.from_string("from views.market_map_page import render_market_map\nrender_market_map()")
            app.session_state["_cloud_last_good"] = (sample_quotes(), meta)
            app.run(timeout=10)
            self.assertFalse(app.exception)
            self.assertTrue(any("上次成功快照" in item.value for item in app.warning))


if __name__ == "__main__":
    unittest.main()
