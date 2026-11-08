"""全市场股票云图：行业树图、市场宽度、筛选与研究入口。"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import streamlit as st
import streamlit.components.v1 as components

from astock.market import market_map as mm
from astock.market.industry_taxonomy import UNCLASSIFIED_INDUSTRY, UNMAPPED_INDUSTRY, industry_parent
from astock.paths import PROJECT_ROOT
from astock.research import research_store
from astock.ui import ui_theme as ut

_cloud = components.declare_component(
    "stock_cloud", path=str(PROJECT_ROOT / "astock/ui/market_map_component"))
_INDUSTRY_CACHE = PROJECT_ROOT / ".astock/market_map_industries.json"
_PREVIEW_WORKERS = 4


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
    if previous and previous_source == "东方财富行业" and source == "新浪行业":
        return previous, f"{previous_source}（缓存）"
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


@st.cache_resource
def _preview_jobs():
    return ThreadPoolExecutor(max_workers=_PREVIEW_WORKERS, thread_name_prefix="cloud-history"), Lock(), {}


def _fetch_preview_history(code):
    try:
        return mm.fetch_preview_history(code)
    except Exception:
        return {"status": "unavailable"}


def _history_response(code, retry=False):
    """只等待已完成的日线请求。同一股票共享请求，并限制并发、缓存大小和有效期。"""
    pool, lock, jobs = _preview_jobs()
    with lock:
        now = time.monotonic()
        for key, (future, started) in list(jobs.items()):
            if future.done():
                ready = future.result()["status"] == "ready"
                ttl = 300 if ready else 30
                if now - started >= ttl or (retry and key == code and not ready):
                    del jobs[key]
        if code not in jobs:
            if sum(not future.done() for future, _ in jobs.values()) >= _PREVIEW_WORKERS:
                return {"status": "loading"}
            if len(jobs) >= 64:
                oldest_done = next(key for key, (future, _) in jobs.items() if future.done())
                del jobs[oldest_done]
            jobs[code] = (pool.submit(_fetch_preview_history, code), now)
        future, _ = jobs[code]
    return future.result() if future.done() else {"status": "loading"}


def _open_research(code):
    st.session_state["_pending_code"] = code
    st.session_state["_pending_period"] = "daily"
    st.session_state["_pending_run"] = True
    st.session_state["_pending_page"] = "analysis"
    st.session_state["_pending_tech_view"] = "overview"


def _handle_preview_event(event, filtered, quotes, meta):
    preview = st.session_state.get("_cloud_preview")
    if not isinstance(event, dict):
        return preview
    nonce = event.get("nonce")
    if nonce is None or nonce == st.session_state.get("_cloud_event_nonce"):
        return preview
    st.session_state["_cloud_event_nonce"] = nonce
    code = event.get("code")
    action = event.get("action", "select")
    if not isinstance(code, str) or action not in ("preview", "poll", "select", "retry", "watch", "research"):
        return preview
    rows = quotes[quotes["代码"] == code]
    if rows.empty:
        return preview
    row = rows.iloc[0]
    if action == "research":
        _open_research(code)
        st.rerun()
    if action == "select" and code in filtered["代码"].values:
        st.session_state["_cloud_selected"] = code
        st.session_state["cloud_pick"] = code

    new_snapshot = not preview or (preview["code"], preview["fetched_at"]) != (code, meta["fetched_at"])
    if new_snapshot:
        preview = mm.preview_snapshot(row, quotes)
        preview.update(source=meta["source"], fetched_at=meta["fetched_at"], watched=False)
    if new_snapshot or action in ("preview", "select"):
        try:
            preview["watched"] = research_store.is_watched(code)
            preview.pop("watch_error", None)
        except (OSError, sqlite3.Error):
            preview["watch_error"] = "暂时无法读取自选列表"
    if action == "watch":
        try:
            research_store.add_watchlist(code, row["名称"])
            preview["watched"] = True
            preview.pop("watch_error", None)
        except (OSError, sqlite3.Error):
            preview["watch_error"] = "未能保存自选，请重试"
    preview["history"] = _history_response(code, retry=action == "retry")
    st.session_state["_cloud_preview"] = preview
    return preview


def _sidebar_summary(stats, market, covered, meta, stale):
    counts = (("上涨", "up"), ("下跌", "down"), ("平盘", "flat"))
    cells = "".join(f'<div><span>{label}</span><b class="{key}">{stats[key]:,}</b></div>'
                    for label, key in counts)
    bar = "".join(f'<i style="width:{stats[key] / (stats["count"] or 1):.2%};background:var(--{key})"></i>'
                  for _, key in counts if stats[key])
    snapshot = "沿用上次快照" if stale else "行情覆盖"
    return (
        '<div class="cloud-side-summary">'
        '<div class="cloud-eyebrow">MARKET BREADTH</div>'
        f'<div class="cloud-side-title"><strong>市场全景</strong><span>{ut.esc(market)}</span></div>'
        f'<div class="cloud-side-counts">{cells}</div>'
        f'<div class="cloud-side-bar" aria-hidden="true">{bar}</div>'
        f'<div class="cloud-side-meta">统计 {stats["count"]:,} 只 · '
        f'{snapshot} {covered:,} / {meta["expected"]:,} 只</div>'
        '</div>'
    )


def _select_industry(names):
    st.session_state["cloud_sectors"] = list(names)


def _select_subindustry(key):
    name = st.session_state.get(key)
    if name:
        _select_industry([name])


def _reset_industry():
    st.session_state["cloud_sectors"] = []
    st.session_state["cloud_industry_search"] = ""
    st.session_state["_cloud_zoom_reset"] = st.session_state.get("_cloud_zoom_reset", 0) + 1


def _industry_label(item, include_count=False):
    name, change = item["name"], item["change"]
    color = "red" if change > 0 else "green" if change < 0 else "gray"
    safe_name = re.sub(r"([\\*_\[\]<>\x60])", r"\\\1", name)
    count = f' :gray[{item["count"]:,} 只]' if include_count else ""
    return f"**{safe_name}** :{color}[{change:+.2f}%]{count}"


def _render_market_sidebar(sidebar, quotes, meta, market, exclude_st, min_cap, sectors, stale):
    # 行业导航不应用个股搜索、涨跌筛选和行业选择，避免定位后丢失其他行业。
    universe = mm.filter_quotes(quotes, market=market, exclude_st=exclude_st, min_cap=min_cap)
    directory = mm.sector_tree(universe)
    by_parent = {}
    for name in quotes["行业"].unique():
        by_parent.setdefault(industry_parent(name), []).append(name)
    with sidebar:
        st.markdown(_sidebar_summary(mm.market_stats(universe), market, len(quotes), meta, stale),
                    unsafe_allow_html=True)
        st.markdown('<div class="cloud-side-title cloud-side-heading"><strong>行业导航</strong>'
                    '<span>申万分级 · 市值加权</span></div>', unsafe_allow_html=True)
        query = st.text_input("搜索行业", placeholder="搜索一级 / 二级行业", key="cloud_industry_search",
                              label_visibility="collapsed", icon=":material/search:").strip().casefold()
        sort, reset = st.columns([1.55, 1])
        order = sort.selectbox("行业排序", ["市值优先", "涨幅优先", "跌幅优先"],
                               label_visibility="collapsed", key="cloud_industry_sort")
        reset.button("看全部", key="cloud_industry_reset", width="stretch", on_click=_reset_industry)
        visible = []
        for group in directory:
            matches_parent = query in group["name"].casefold()
            children = [item for item in group["children"] if item["name"] != group["name"]
                        and (matches_parent or query in item["name"].casefold())]
            if matches_parent or children:
                visible.append((group, children))
        if order != "市值优先":
            ascending = order == "跌幅优先"
            visible.sort(key=lambda item: item[0]["change"], reverse=not ascending)
            for _, children in visible:
                children.sort(key=lambda item: item["change"], reverse=not ascending)
        visible.sort(key=lambda item: item[0]["name"] in (UNCLASSIFIED_INDUSTRY, UNMAPPED_INDUSTRY))
        st.markdown(f'<div class="cloud-side-meta">显示 {len(visible)} / {len(directory)} 组'
                    ' · 展开查看二级行业</div>', unsafe_allow_html=True)
        if not visible:
            st.caption("当前范围暂无匹配行业。")
            return

        with st.container(height=400 if len(visible) > 8 else "content", border=False, key="cloud_industry_list"):
            for group, children in visible:
                parent = group["name"]
                members = by_parent[parent]
                expanded = bool(query) or any(name in sectors for name in members)
                # 搜索使用单独的展开状态，避免沿用目录中的收起状态。
                with st.expander(_industry_label(group, include_count=True), expanded=expanded,
                                 key=f"cloud_l1_{parent}_{hash(query)}"):
                    st.button("查看该组全部成分股", key=f"cloud_focus_{parent}", width="stretch",
                              on_click=_select_industry, args=(members,))
                    if children:
                        st.caption("原始分类" if parent == UNMAPPED_INDUSTRY else "二级行业")
                        by_name = {item["name"]: item for item in children}
                        key = f"cloud_l2_{parent}"
                        active = sectors[0] if len(sectors) == 1 and sectors[0] in by_name else None
                        if st.session_state.get(key) != active:
                            st.session_state[key] = active
                        st.radio("选择细分行业", list(by_name), index=None,
                                 format_func=lambda name, items=by_name: _industry_label(items[name]),
                                 captions=[f'{item["count"]:,} 只' for item in children],
                                 width="stretch", label_visibility="collapsed", key=key,
                                 on_change=_select_subindustry, args=(key,))


def render_market_map(sidebar=None):
    if sidebar is None:
        sidebar = st.sidebar.container(key="cloud_sidebar")
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
            sidebar.markdown("**市场全景**")
            sidebar.caption("等待行情快照")
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
        selected = st.session_state.get("cloud_sectors", [])
        if "银行Ⅱ" in selected:
            selected = [*selected, *(s for s in sector_options if industry_parent(s) == "银行")]
        st.session_state["cloud_sectors"] = [s for s in dict.fromkeys(selected) if s in sector_options]
        sectors = c2.multiselect("行业筛选", sector_options, key="cloud_sectors", placeholder="全部行业")
        direction = c3.selectbox("涨跌筛选", ("全部", "仅上涨", "仅下跌"), key="cloud_direction")
        exclude_st = c4.checkbox("剔除 ST / 退市", value=True, key="cloud_exclude_st")

    _render_market_sidebar(sidebar, quotes, meta, market, exclude_st, min_cap, sectors, stale)
    filtered = mm.filter_quotes(quotes, market, sectors, query, exclude_st, direction, min_cap)
    if filtered.empty:
        st.info("没有符合条件的股票，请调整市场、行业或搜索条件。")
        return
    stats = mm.market_stats(filtered)
    groups, omitted = mm.map_payload(filtered, area)
    covered = int((quotes["行业"] != UNCLASSIFIED_INDUSTRY).sum())
    fetched = meta["fetched_at"].replace("T", " ").replace("+08:00", "")
    st.markdown(
        f'<div class="cloud-meta"><span>显示 <b>{stats["count"] - omitted:,}</b> / {len(quotes):,} 只有效行情'
        f' <em>涨跌中位数 {stats["median"]:+.2f}% · 成交 {mm.format_amount(stats["amount"])}'
        f' · 面积 {ut.esc(area)}</em></span>'
        f'<span>{ut.esc(meta["source"])} · 获取于 {ut.esc(fetched)} 北京时间</span></div>',
        unsafe_allow_html=True)
    if omitted:
        st.caption(f"{omitted} 只股票的{area}缺失或为零，未参与面积绘制；上方统计仍包含这些股票。可切换「等权」查看。")

    _render_map_display(groups, filtered, quotes, meta, height, fit_window, str(time.monotonic_ns()))
    st.caption(f"休市期间显示行情源最近返回的快照。行业层级按申万分类整理，"
               f"成分归属基于{industry_source}，覆盖 {covered:,} / {len(quotes):,} 只。")


@st.fragment
def _render_map_display(groups, filtered, quotes, meta, height, fit_window, view_id):
    # 悬停查询只更新云图区域，沿用当前市场快照。
    preview = _handle_preview_event(st.session_state.get("market_cloud"), filtered, quotes, meta)
    _cloud(groups=groups, height=height, auto_height=fit_window, selected=st.session_state.get("_cloud_selected"),
           view_id=view_id, preview=preview, zoom_reset=st.session_state.get("_cloud_zoom_reset", 0),
           key="market_cloud", default=None)

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
            if action.button("进入个股研究 ↗", type="primary", width="stretch"):
                _open_research(code)
                st.rerun()
        else:
            detail.caption("从市场全景找到关注标的，再展开技术、基本面与事件研究。")
