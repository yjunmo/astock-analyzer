from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from astock.market import screener
from astock.paths import PROJECT_ROOT
from astock.ui import state


def _choose_strategy(app, strategy):
    selector = next(item for item in app.get("button_group") if item.key == "short_strategy")
    return selector.set_value(strategy).run()


def test_scan_route_loads_only_requested_data():
    pool = pd.DataFrame({"代码": ["000001", "600001"], "名称": ["天马股份", "示例公司"],
                         "所属行业": ["电子", "电子"], "连板数": [2, 1]})
    sector_rows = screener.rank_sector_boards(pd.DataFrame({
        "板块": [f"概念{i}" for i in range(35)] + ["下跌概念", "平盘概念", "缺少领涨股"],
        "涨跌幅": [1.0] * 35 + [-1.0, 0.0, -2.0],
        "股票代码": ["sh600001"] * 35 + ["sz000002", "bj920001", "-"],
        "股票名称": ["示例公司"] * 35 + ["ST示例", "北交示例", "-"],
        "个股-涨跌幅": [2.0] * 35 + [-0.5, 0.0, None],
    }))
    with patch.object(state, "load_short_scan", side_effect=[
        (pool, "20261106", "11-07 10:00:00"), (sector_rows, "", "11-07 10:00:01")
    ]) as load, patch.object(state, "fetch_spot_market_live") as activity, \
            patch.object(state, "fetch_realtime_cached") as realtime, \
            patch.object(state, "load_history") as history:
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.session_state["main_nav"] = "screener"
        app.session_state["active_query"] = ("sz000001", "daily", "qfq")
        app.session_state["code_field"] = "000001"
        app.run()
        assert not app.exception
        load.assert_not_called()
        _choose_strategy(app, "limit")
        load.assert_not_called()
        app.button(key="short_scan_limit").click().run()
        assert not app.exception
        load.assert_called_once_with("limit")
        app.radio(key="short_streak_type").set_value("首板").run()
        assert app.dataframe[0].value["代码"].tolist() == ["600001"]
        _choose_strategy(app, "emotion")
        app.radio(key="short_emotion_mode").set_value("生肖简称").run()
        app.selectbox(key="short_zodiac").select("马").run()
        assert app.dataframe[0].value["代码"].tolist() == ["000001"]
        load.assert_called_once_with("limit")

        _choose_strategy(app, "leaders")
        app.radio(key="short_board_type").set_value("概念").run()
        load.assert_called_once_with("limit")
        app.button(key="short_scan_leaders").click().run()
        assert not app.exception
        assert len(app.dataframe[0].value) == 38
        assert any("显示 38 / 38 个概念板块" in item.value for item in app.caption)
        assert app.selectbox(key="short_pick_concept").options == [
            "600001 示例公司", "920001 北交示例", "000002 ST示例"
        ]
        app.radio(key="short_board_trend_concept").set_value("下跌").run()
        assert app.dataframe[0].value["板块"].tolist() == ["下跌概念", "缺少领涨股"]
        assert app.selectbox(key="short_pick_concept").options == ["000002 ST示例"]
        assert load.call_args.args == ("concept",)
        assert load.call_count == 2
        activity.assert_not_called()
        realtime.assert_not_called()
        history.assert_not_called()


def test_manual_activity_scan_is_scoped_and_does_not_repeat_on_rerun():
    quotes = pd.DataFrame({"代码": ["000001", "300001"], "名称": ["示例主板", "示例创业"],
                           "最新价": [10, 20], "涨跌幅": [6, 12], "成交额": [3e8, 4e8]})
    with patch.object(state, "fetch_spot_market_live", return_value=(quotes, "sina", True)) as fetch:
        app = AppTest.from_string("from views.shortpage import render_short_page\nrender_short_page({})")
        app.run()
        _choose_strategy(app, "activity")
        fetch.assert_not_called()
        app.button(key="st_scan").click().run()
        assert not app.exception
        fetch.assert_called_once_with(include_gex=True, max_age=0)
        assert len(app.dataframe) == 2
        app.run()
        fetch.assert_called_once()
        app.toggle(key="st_inc_gex").set_value(False).run()
        assert not app.dataframe
        fetch.assert_called_once()


def test_weekend_and_preopen_use_latest_started_session():
    calendar = pd.DataFrame({"trade_date": ["2026-11-06", "2026-11-09"]})
    assert screener.latest_scan_date(calendar, pd.Timestamp("2026-11-07T10:00+08:00")) == "20261106"
    assert screener.latest_scan_date(calendar, pd.Timestamp("2026-11-09T09:15+08:00")) == "20261106"
    assert screener.latest_scan_date(calendar, pd.Timestamp("2026-11-09T09:30+08:00")) == "20261109"
