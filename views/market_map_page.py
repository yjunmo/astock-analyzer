"""全市场股票云图：行业树图、市场宽度、筛选与研究入口。"""
from __future__ import annotations

import json
import time
import streamlit as st
import streamlit.components.v1 as components

from astock.market import market_map as mm
from astock.paths import PROJECT_ROOT
from astock.ui import ui_theme as ut

_cloud = components.declare_component(
    "stock_cloud", path=str(PROJECT_ROOT / "astock/ui/market_map_component"))
_INDUSTRY_CACHE = PROJECT_ROOT / ".astock/market_map_industries.json"


@st.cache_data(ttl=86400, show_spinner=False)
def _load_industries():
    previous = {}
    previous_source = "行业源"
    try:
        saved = json.loads(_INDUSTRY_CACHE.read_text(encoding="utf-8"))
        previous = saved.get("mapping", {})
        previous_source = saved.get("source", "新浪行业")
        if previous and time.time() - saved.get("ts", 0) < 7 * 86400:
            return previous, previous_source
    except (OSError, ValueError):
        pass
    try:
        mapping, source = mm.fetch_industries()
    except Exception:
        return previous, previous_source
    if mapping:
        # 采集不完整时保留上次已知行业，不用失败结果覆盖成功缓存。
        if source == previous_source:
            mapping = {**previous, **mapping}
        try:
            _INDUSTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _INDUSTRY_CACHE.write_text(json.dumps({"ts": time.time(), "mapping": mapping, "source": source},
                                                ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return (mapping, source) if mapping else (previous, previous_source)


@st.cache_data(ttl=120, show_spinner=False)
def _load_quotes():
    return mm.fetch_quotes()


def _pick_stock():
    code = st.session_state.get("cloud_pick")
    if code:
        st.session_state["_cloud_selected"] = code


def _cloud_changed():
    event = st.session_state.get("market_cloud")
    if isinstance(event, dict) and event.get("code"):
        st.session_state["_cloud_selected"] = event["code"]
        st.session_state["cloud_pick"] = event["code"]


def _open_research(code):
    st.session_state["_pending_code"] = code
    st.session_state["_pending_period"] = "daily"
    st.session_state["_pending_run"] = True
    st.session_state["_pending_page"] = "analysis"
    st.session_state["_pending_tech_view"] = "overview"


def _summary(stats):
    n = stats["count"] or 1
    return (
        '<div class="cloud-summary">'
        '<div class="cloud-overview"><span class="cloud-eyebrow">MARKET BREADTH</span>'
        f'<strong>{stats["up_ratio"]:.1f}<small>%</small></strong><span>当前范围 · 上涨占比</span></div>'
        '<div class="cloud-breadth"><div class="cloud-bar">'
        f'<i style="width:{stats["up"] / n * 100}%;background:var(--up)"></i>'
        f'<i style="width:{stats["flat"] / n * 100}%;background:#495565"></i>'
        f'<i style="width:{stats["down"] / n * 100}%;background:var(--down)"></i></div>'
        f'<div class="cloud-counts"><span class="pos">上涨 {stats["up"]:,}</span>'
        f'<span>平盘 {stats["flat"]:,}</span><span class="neg">下跌 {stats["down"]:,}</span></div></div>'
        f'<div class="cloud-stat"><span>涨跌幅中位数</span><strong class="{"pos" if stats["median"] > 0 else "neg" if stats["median"] < 0 else ""}">{stats["median"]:+.2f}%</strong></div>'
        f'<div class="cloud-stat"><span>成交总额</span><strong>{mm.format_amount(stats["amount"])}</strong></div></div>'
    )


def render_market_map():
    st.markdown(
        '<div class="cloud-heading"><div><div class="cloud-eyebrow">A-SHARE MARKET MAP</div>'
        '<div class="cloud-title" role="heading" aria-level="1">股票云图<span>一眼看清，市场全貌。</span></div></div>'
        '<span class="cloud-session"><i></i> 当日涨跌 · 最近行情快照</span></div>',
        unsafe_allow_html=True)

    top1, top2, top3 = st.columns([2.7, 1.6, 1.4])
    with top1:
        market = st.segmented_control("市场范围", mm.MARKETS, default="全部A股",
                                      key="cloud_market", label_visibility="collapsed") or "全部A股"
    with top2:
        query = st.text_input("搜索股票", placeholder="搜索股票名称 / 6 位代码", key="cloud_search",
                              label_visibility="collapsed", icon=":material/search:")
    with top3:
        with st.container(key="cloud_actions"):
            r1, r2 = st.columns(2)
            refresh = r1.button("刷新行情", icon=":material/refresh:", width="stretch")
            with r2.popover("显示设置", icon=":material/tune:", width="stretch"):
                min_cap = st.number_input("最低总市值（亿元）", min_value=0, value=0, step=100, key="cloud_min_cap")
                fit_window = st.toggle("云图填满可视区域", value=True, key="cloud_fit_window")
                height = st.slider("云图高度", 420, 1000, 640, 20, key="cloud_height", disabled=fit_window)
    if refresh:
        _load_quotes.clear()
        _load_industries.clear()

    stale = False
    try:
        with st.spinner("正在拼合全市场行情…"):
            quotes, meta = _load_quotes()
        # 部分请求失败时也可展示；完整度明显下降时优先保留会话内成功快照。
        previous = st.session_state.get("_cloud_last_good")
        if previous and len(quotes) < len(previous[0]) * .8:
            quotes, meta = previous
            stale = True
        else:
            st.session_state["_cloud_last_good"] = (quotes, meta)
    except Exception:
        previous = st.session_state.get("_cloud_last_good")
        if not previous:
            st.warning("暂时无法连接行情源，请稍后点击「刷新行情」重试。")
            return
        quotes, meta = previous
        stale = True

    with st.spinner("正在加载行业分类…"):
        industries, industry_source = _load_industries()
    quotes = mm.normalize_quotes(quotes, industries)
    if stale:
        st.warning("行情更新失败或覆盖不足，当前保留上次成功快照；请留意下方获取时间。")
    if meta.get("failed_pages"):
        st.warning(f"部分行情页暂未返回：已获取 {len(quotes):,} / {meta['expected']:,} 只，仅统计已返回的有效行情。")

    with st.container(border=True, key="cloud_filters"):
        c1, c2, c3, c4 = st.columns([1.15, 2, 1, .9])
        area = c1.selectbox("方块面积", mm.AREA_FIELDS, key="cloud_area")
        sector_options = sorted(quotes["行业"].unique().tolist())
        st.session_state["cloud_sectors"] = [s for s in st.session_state.get("cloud_sectors", []) if s in sector_options]
        sectors = c2.multiselect("行业筛选", sector_options, key="cloud_sectors", placeholder="全部行业")
        direction = c3.selectbox("涨跌筛选", ("全部", "仅上涨", "仅下跌"), key="cloud_direction")
        exclude_st = c4.checkbox("剔除 ST / 退市", value=True, key="cloud_exclude_st")

    filtered = mm.filter_quotes(quotes, market, sectors, query, exclude_st, direction, min_cap)
    if filtered.empty:
        st.info("没有符合条件的股票，请调整市场、行业或搜索条件。")
        return
    stats = mm.market_stats(filtered)
    groups, omitted = mm.map_payload(filtered, area)
    st.markdown(_summary(stats), unsafe_allow_html=True)
    covered = int((quotes["行业"] != "未分类").sum())
    fetched = meta["fetched_at"].replace("T", " ").replace("+08:00", "")
    st.markdown(
        f'<div class="cloud-meta"><span>显示 <b>{stats["count"] - omitted:,}</b> / {len(quotes):,} 只有效行情'
        f' <em>面积：{ut.esc(area)} · 颜色：当日涨跌幅</em></span>'
        f'<span>{ut.esc(meta["source"])} · 获取于 {ut.esc(fetched)} 北京时间</span></div>',
        unsafe_allow_html=True)
    if omitted:
        st.caption(f"{omitted} 只股票的{area}缺失或为零，未参与面积绘制；上方统计仍包含这些股票。可切换「等权」查看。")

    _cloud(groups=groups, height=height, auto_height=fit_window, selected=st.session_state.get("_cloud_selected"),
           key="market_cloud", default=None, on_change=_cloud_changed)

    swatches = [("≤ −5%", "#25b083"), ("−3%", "#229a75"), ("−1%", "#25836a"),
                ("−0.5%", "#286c5c"), ("0%", "#3b4756"), ("+0.5%", "#95414d"),
                ("+1%", "#b93f50"), ("+3%", "#dc445a"), ("≥ +5%", "#f24c64")]
    legend = "".join(f'<span><i style="background:{color}"></i>{name}</span>' for name, color in swatches)
    st.markdown(f'<div class="cloud-legend"><div>{legend}</div><span>红涨绿跌 · 行业涨幅按总市值加权</span></div>', unsafe_allow_html=True)

    with st.container(border=True):
        pick, detail, action = st.columns([1.25, 2, 1])
        options = filtered.sort_values("总市值", ascending=False)["代码"].tolist()
        names = filtered.set_index("代码")["名称"].to_dict()
        current = st.session_state.get("_cloud_selected")
        if st.session_state.get("cloud_pick") not in options:
            st.session_state["cloud_pick"] = current if current in options else None
        code = pick.selectbox("选中股票", options, index=None,
                              key="cloud_pick", format_func=lambda c: f"{names[c]} · {c}",
                              placeholder="点击云图选股，或在此搜索", on_change=_pick_stock)
        if code:
            row = filtered[filtered["代码"] == code].iloc[0]
            detail.markdown(f'<div class="cloud-pick"><b>{ut.esc(row["名称"])}</b>'
                            f'<strong class="{"pos" if row["涨跌幅"] > 0 else "neg" if row["涨跌幅"] < 0 else ""}">{row["最新价"]:.2f} <small>{row["涨跌幅"]:+.2f}%</small></strong>'
                            f'<span>{ut.esc(row["行业"])} · 市值 {mm.format_amount(row["总市值"])}</span></div>', unsafe_allow_html=True)
            action.button("进入个股研究 ↗", type="primary", width="stretch", on_click=_open_research, args=(code,))
        else:
            detail.caption("从市场全景找到关注标的，再展开技术、基本面与事件研究。")

    st.caption(f"数据口径：最新可得快照，非逐笔行情；休市期间展示行情源最近返回值。"
               f"行业采用{industry_source}，已覆盖 {covered:,} / {len(quotes):,} 只，其余归入「未分类」；无有效报价的股票不参与统计。")
