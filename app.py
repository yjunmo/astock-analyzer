import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import pandas as pd
import streamlit as st

from astock.market import data_fetcher as dtf
from astock.ui import state
from astock.ui import ui_theme as ut
from astock.analysis.report import build_report
from views.news_page import render_news_intel
from views.market_map_page import render_market_map
from views.pulse_page import render_event_pulse
from views.research_page import render_research_page
from views.shortpage import render_short_page
from views.sidebar import render_ai_settings, render_search_settings, render_sidebar
from views.tech import render_tech_panel

# akshare 等库的请求不带超时，接口卡死会阻塞脚本线程，
# 导致页面无响应且 Ctrl+C 后「Stopping...」长时间停不下来；
# 进程级兜底默认超时，显式 timeout（如 AI 客户端）不受影响
dtf.install_default_request_timeout()

st.set_page_config(page_title="A股研究台", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")

NAV_LABELS = {
    "market_map": "股票云图",
    "analysis": "研究工作台",
    "library": "个人研究库",
    "screener": "短线扫描",
    "news": "个股情报",
    "pulse": "全球要闻",
}


def _fmt_amt(v):
    if v is None:
        return "--"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.0f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f} 万"
    return f"{v:.0f}"


def _build_bundle(symbol: str, period: str, adjust: str, n_bars: int):
    """装配单只股票的完整数据包：行情/指标/报告（会话缓存，rerun 不重算）。

    实时行情走 state 层 TTL 缓存；完整 bundle（含 build_report 结果）按
    (symbol, period, adjust, n_bars) 缓存，上限 8 条防会话内无界增长。
    """
    key = ("bundle", symbol, period, adjust, n_bars)
    cached = st.session_state.get(key)
    if cached is not None:
        return cached
    snap = state.fetch_realtime_cached(symbol)
    name = snap.get("name") or symbol[2:]
    is_st = dtf.is_st_name(name)
    df_all, err = state.ensure_df_all(symbol, period, adjust, is_st)
    if err:
        return {"load_failed": True, "err": err}
    if len(df_all) < 2:
        return {"load_failed": True, "err": "有效K线不足，无法分析。"}
    suffix = {"sh": "SH", "sz": "SZ"}.get(symbol[:2], "BJ")
    symbol_disp = f"{symbol[2:]}.{suffix}"
    result = {
        "load_failed": False,
        "bundle": {
            "symbol": symbol, "period": period, "adjust": adjust,
            "n_bars": n_bars, "snap": snap, "name": name,
            "is_st": is_st, "df_all": df_all,
            "df": df_all.tail(n_bars).reset_index(drop=True),
            "symbol_disp": symbol_disp,
            "result": build_report(df_all, name=name, symbol=symbol_disp,
                                   period=period),
        },
    }
    st.session_state[key] = result
    order = st.session_state.setdefault("_bundle_order", [])
    order.append(key)
    while len(order) > 8:
        old_key = order.pop(0)
        if old_key != key:
            st.session_state.pop(old_key, None)
    return result


def _render_hero(bundle: dict):
    symbol, period, adjust = bundle["symbol"], bundle["period"], bundle["adjust"]
    snap, name, is_st = bundle["snap"], bundle["name"], bundle["is_st"]
    df_all = bundle["df_all"]
    suffix = {"sh": "SH", "sz": "SZ"}.get(symbol[:2], "BJ")
    symbol_disp = f"{symbol[2:]}.{suffix}"
    period_label = {"daily": "日线", "weekly": "周线", "monthly": "月线"}[period]
    adjust_label = {"qfq": "前复权", "hfq": "后复权", "": "不复权"}[adjust]

    last_close = float(df_all["close"].iloc[-1])
    prev_close = float(df_all["close"].iloc[-2])
    use_rt = bool(snap and snap.get("price"))
    price = float(snap["price"]) if use_rt else last_close
    prev_ref = float(snap["prev_close"] or prev_close) if use_rt else prev_close
    chg_pct = (price / prev_ref - 1) * 100 if prev_ref else 0.0
    if use_rt:
        ohlc = {"今开": f"{snap['open']:.2f}", "最高": f"{snap['high']:.2f}",
                "最低": f"{snap['low']:.2f}", "昨收": f"{snap['prev_close']:.2f}",
                "成交额": _fmt_amt(snap.get("amount"))}
        ts = f"{snap.get('date', '')} {snap.get('time', '')}".strip()
    else:
        r = df_all.iloc[-1]
        ohlc = {"今开": f"{r['open']:.2f}", "最高": f"{r['high']:.2f}",
                "最低": f"{r['low']:.2f}", "昨收": f"{prev_close:.2f}"}
        ts = pd.Timestamp(r["date"]).strftime("%Y-%m-%d 收盘")

    badges = [period_label, adjust_label, f"共{len(df_all)}个已收盘周期"]
    if is_st:
        badges.append("ST")
    badges.append("涨跌停按不复权价·除权日或有偏差")
    st.markdown(ut.hero(
        name=name, symbol_disp=symbol_disp, badges=badges,
        price=price, chg_pct=chg_pct, prev_price=prev_ref,
        ohlc=ohlc, ts=ts, closed_only=not use_rt,
    ), unsafe_allow_html=True)
    st.caption("数据来源：新浪财经 / 腾讯证券（经 akshare 接口）· 技术指标为通达信口径 · "
               "「消息情报」追踪个股与题材催化 · 「全科新闻」按板块看全球要闻 · "
               "仅供学习研究，不构成任何投资建议")
    return symbol_disp


def _render_report_actions(bundle: dict):
    """轻量操作栏：导出当前规则报告，不触发任何额外网络请求。"""
    result = bundle["result"]
    code6 = bundle["symbol"][2:]
    c1, c2, c3 = st.columns([1, 1, 5])
    c1.download_button(
        "导出研究报告", data=result["text"],
        file_name=f"{code6}_{bundle['period']}_report.md",
        mime="text/markdown", width="stretch", icon=":material/download:")
    if c2.button("刷新数据", width="stretch", icon=":material/refresh:",
                 help="清除当前会话中的行情与分析结果后重新拉取"):
        query = (bundle["symbol"], bundle["period"], bundle["adjust"])
        state.load_history.clear(bundle["symbol"], bundle["period"],
                                 bundle["adjust"], bundle["is_st"])
        if bundle["adjust"]:
            state._load_raw_daily.clear(bundle["symbol"])
        st.session_state.pop("df_all", None)
        st.session_state.pop("data_key", None)
        for key in list(st.session_state):
            if (isinstance(key, tuple) and key[:4] ==
                    ("bundle", bundle["symbol"], bundle["period"],
                     bundle["adjust"])):
                st.session_state.pop(key, None)
        state.set_active_query(query)
        st.rerun()
    c3.caption("报告基于已收盘 K 线与规则信号生成 · Markdown 可直接归档")


def main():
    st.markdown(ut.apply_global_css(), unsafe_allow_html=True)

    # 短线选股/快捷按钮传入的待选标的：必须在侧边栏 widget 实例化之前写入
    pending = st.session_state.pop("_pending_code", None)
    if pending:
        st.session_state["code_field"] = pending
        if "_pending_period" in st.session_state:
            st.session_state["period_radio"] = st.session_state.pop(
                "_pending_period")
        # 未显式指定周期（如快捷按钮）时保持用户当前周期选择

    pending_page = st.session_state.pop("_pending_page", None)
    if pending_page in NAV_LABELS:
        st.session_state["main_nav"] = pending_page
    pending_tech = st.session_state.pop("_pending_tech_view", None)
    if pending_tech:
        st.session_state["tech_view"] = pending_tech

    code, period, adjust, n_bars, run = render_sidebar()
    ai = render_ai_settings()
    render_search_settings()

    st.markdown(ut.brand_header(), unsafe_allow_html=True)
    if run or st.session_state.get("_pending_run"):
        st.session_state["main_nav"] = "analysis"
    st.session_state.setdefault("main_nav", "market_map")
    page = st.segmented_control(
        "主导航", list(NAV_LABELS), key="main_nav",
        format_func=lambda x: NAV_LABELS[x], label_visibility="collapsed",
        width="stretch")
    page = page or "market_map"

    bundle = None
    stock_info = None
    stock_err = None
    load_failed = False

    code_str = str(code or "").strip()
    if page not in ("analysis", "news", "library", "screener"):
        pass  # 全市场页面独立加载，避免切页时触发个股行情和报告计算。
    elif not (len(code_str) == 6 and code_str.isdigit()):
        stock_info = ("请在左侧输入6位股票代码（如 600519 / 000001），或点击快捷按钮。"
                      "「短线选股」页可扫描全市场候选，「全科新闻」页也无需选股。")
    else:
        try:
            symbol = dtf.normalize_symbol(code)
        except ValueError as e:
            stock_err = str(e)
        else:
            triggered = run or state.pending_rerun_marker()
            query = (symbol, period, adjust)
            if triggered:
                state.set_active_query(query)
            if state.active_query() != query:
                stock_info = ("请点击「开始分析」加载或刷新该标的（修改周期/复权后也需重新分析）。"
                              "「全科新闻」页可直接使用。")
            else:
                result = _build_bundle(symbol, period, adjust, n_bars)
                if result["load_failed"]:
                    load_failed = True
                    stock_err = result["err"]
                else:
                    bundle = result["bundle"]

    if bundle and page in ("analysis", "news", "library"):
        _render_hero(bundle)

    if bundle and page == "analysis":
        _render_report_actions(bundle)

    # 使用显式路由而非 st.tabs：Streamlit 的 tabs 会把隐藏页也执行一遍，
    # 这里确保基本面、全市场扫描和新闻抓取只在用户真正进入时运行。
    if page == "market_map":
        render_market_map()
    elif page == "analysis":
        if bundle:
            render_tech_panel(ai, bundle)
        elif stock_err:
            st.error(stock_err)
        elif load_failed:
            pass
        else:
            st.markdown(ut.empty_state(), unsafe_allow_html=True)
            if stock_info and code_str:
                st.info(stock_info)
    elif page == "screener":
        render_short_page(ai, bundle)
    elif page == "news":
        if bundle:
            render_news_intel(ai, code6=bundle["symbol"][2:], name=bundle["name"])
        elif stock_err:
            st.error(stock_err)
        elif load_failed:
            pass
        else:
            st.info("先在左侧分析一只股票，即可建立该标的的消息情报流。")
    elif page == "pulse":
        render_event_pulse(ai)
    elif page == "library":
        render_research_page(bundle)

    st.divider()
    st.caption("⚠️ 免责声明：本工具输出仅为基于历史数据的技术指标计算结果与模型推演，"
               "不构成任何投资建议。股市有风险，入市需谨慎。")


if __name__ == "__main__":
    main()
