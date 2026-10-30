"""个人研究档案页：把一次性分析变成可追踪、可复盘的研究流程。"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data_fetcher as dtf
import monitor_service
import research_analytics
import research_services as services
import research_store as store
import ui_theme as ut


def _open_stock(code: str) -> None:
    st.session_state["_pending_code"] = code
    st.session_state["_pending_run"] = True
    st.session_state["_pending_page"] = "analysis"


def _active_price(bundle: dict) -> tuple[float, float]:
    df = bundle["df_all"]
    last = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2]) if len(df) > 1 else last
    snap = bundle.get("snap") or {}
    price = float(snap.get("price") or last)
    ref = float(snap.get("prev_close") or prev)
    return price, ((price / ref - 1) * 100 if ref else 0.0)


def _render_active(bundle: dict) -> None:
    code = bundle["symbol"][2:]
    name = bundle["name"]
    latest = store.latest_thesis(code) or {}
    st.markdown(ut.sec_header("当前标的研究卡", "记录假设，也记录什么情况会证明自己错了"),
                unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.1, 1.1, 3])
    watched = store.is_watched(code)
    if c1.button("移出自选" if watched else "加入自选", type="primary" if not watched else "secondary",
                 width="stretch", key=f"watch_{code}"):
        if watched:
            store.remove_watchlist(code)
        else:
            store.add_watchlist(code, name)
        st.rerun()
    price, chg = _active_price(bundle)
    if c2.button("保存研究快照", width="stretch", key=f"snapshot_{code}"):
        from analytics import build_snapshot
        technical_snapshot = build_snapshot(bundle["df_all"], bundle.get("result") or {})
        payload = {"period": bundle["period"], "adjust": bundle["adjust"],
                   "bars": len(bundle["df_all"]), "technical": technical_snapshot}
        thesis_now = store.latest_thesis(code) or {}
        try:
            import state
            fundamental_snap = state.fetch_fundamentals(code)
        except Exception:
            fundamental_snap = {}
        events_now = services.list_events(code, 100)
        store.save_snapshot(code, name, price=price, chg_pct=chg,
                            report_text=bundle["result"].get("text", ""), payload=payload,
                            fundamentals=fundamental_snap,
                            news={"evidence_count": len(store.list_evidence(code, 500)),
                                  "event_count": len(events_now),
                                  "event_types": sorted({e["event_type"] for e in events_now})},
                            thesis=thesis_now)
        st.success("已保存当前行情与规则报告，可用于后续复盘。")
    c3.caption(f"{name} {code} · 当前参考价 {price:.2f} · {chg:+.2f}%")

    with st.form(f"thesis_form_{code}"):
        a, b = st.columns(2)
        thesis = a.text_area("核心投资假设", value=latest.get("thesis", ""), height=130,
                             placeholder="公司的哪项变化会驱动盈利或估值？")
        invalidation = b.text_area("失效条件（必须可验证）", value=latest.get("invalidation", ""),
                                   height=130, placeholder="出现哪些事实后应承认判断错误？")
        catalysts = a.text_area("未来催化剂", value=latest.get("catalysts", ""), height=100,
                                placeholder="财报、产品、价格、政策、产能、订单……")
        risks = b.text_area("主要风险与反方证据", value=latest.get("risks", ""), height=100,
                            placeholder="竞争、需求、监管、估值、治理……")
        d1, d2 = st.columns(2)
        conviction = d1.slider("信念强度", 1, 5, int(latest.get("conviction", 3)),
                               help="不是目标价，而是当前证据对核心假设的支持程度")
        default_review = date.today() + timedelta(days=30)
        try:
            if latest.get("next_review"):
                default_review = date.fromisoformat(latest["next_review"])
        except ValueError:
            pass
        next_review = d2.date_input("下次强制复核", value=default_review)
        e1, e2 = st.columns(2)
        validation_metrics = e1.text_input(
            "验证指标", value=latest.get("validation_metrics", ""),
            placeholder="收入增速、毛利率、订单、产品价格等")
        valid_default = date.today() + timedelta(days=90)
        try:
            if latest.get("valid_until"):
                valid_default = date.fromisoformat(latest["valid_until"])
        except ValueError:
            pass
        valid_until = e2.date_input("假设有效期", value=valid_default)
        projects = services.list_projects("active")
        project_map = {"不关联项目": None, **{p["title"]: p["id"] for p in projects}}
        project_label = st.selectbox("责任项目", list(project_map))
        if st.form_submit_button("保存为新版本", type="primary", width="stretch"):
            if not (thesis.strip() or invalidation.strip()):
                st.warning("至少填写核心假设或失效条件。")
            else:
                store.save_thesis(code, name, thesis=thesis, catalysts=catalysts,
                                  risks=risks, invalidation=invalidation,
                                  conviction=conviction, next_review=next_review.isoformat(),
                                  validation_metrics=validation_metrics,
                                  valid_until=valid_until.isoformat(),
                                  project_id=project_map[project_label])
                st.success("研究假设已保存为新版本，旧版本会保留用于复盘。")
                st.rerun()


def _render_watchlist() -> None:
    rows = store.list_watchlist()
    st.markdown(ut.sec_header("个人自选", "每个标的都应有假设、失效条件和下一次复核日期"),
                unsafe_allow_html=True)
    if not rows:
        st.info("还没有自选股。分析任意标的后，可在本页加入自选并建立研究假设。")
        return
    due = [r for r in rows if r.get("next_review") and r["next_review"] <= date.today().isoformat()]
    if due:
        st.warning("有 " + str(len(due)) + " 个研究假设已到复核日："
                   + "、".join((r.get("name") or r["symbol"]) for r in due))
    for i, row in enumerate(rows):
        cols = st.columns([2.4, 1.2, 1.2, 1.15, .85])
        cols[0].markdown(f"**{row['name'] or row['symbol']}**  `{row['symbol']}`")
        cols[1].caption(f"证据 {row.get('evidence_count', 0)} 条")
        review = row.get("next_review") or "未设定"
        due_mark = " ⚠️" if row.get("next_review") and review <= date.today().isoformat() else ""
        cols[2].caption(f"复核 {review}{due_mark}")
        if cols[3].button("打开研究", key=f"open_watch_{i}", width="stretch"):
            _open_stock(row["symbol"])
            st.rerun()
        if cols[4].button("移除", key=f"rm_watch_{i}", width="stretch"):
            store.remove_watchlist(row["symbol"])
            st.rerun()


def _render_history(code: str) -> None:
    theses = store.thesis_history(code)
    snaps = store.list_snapshots(code)
    evidence = store.list_evidence(code)
    tab1, tab2, tab3 = st.tabs([f"假设版本 {len(theses)}", f"研究快照 {len(snaps)}",
                                f"证据库 {len(evidence)}"])
    with tab1:
        if not theses:
            st.caption("尚未保存投资假设。")
        for row in theses:
            with st.expander(f"{row['created_at']} · 信念 {row['conviction']}/5 · 复核 {row['next_review'] or '未设定'}"):
                st.markdown(f"**核心假设**\n\n{row['thesis'] or '—'}")
                st.markdown(f"**失效条件**\n\n{row['invalidation'] or '—'}")
                st.markdown(f"**催化剂**\n\n{row['catalysts'] or '—'}")
                st.markdown(f"**风险与反方证据**\n\n{row['risks'] or '—'}")
    with tab2:
        if not snaps:
            st.caption("尚未保存研究快照。")
        for row in snaps:
            label = f"{row['created_at']} · {row.get('price') or 0:.2f} · {row.get('chg_pct') or 0:+.2f}%"
            with st.expander(label):
                st.markdown(row.get("report_text") or "（快照无文本报告）")
    with tab3:
        if st.button("增量补全网页/PDF正文", key=f"hydrate_evidence_{code}",
                     help="优先处理尚未保存正文的官方、媒体和网页证据"):
            with st.spinner("正在读取原文并更新全文索引…"):
                result = services.hydrate_evidence_content(code, limit=20, include_failed=True)
            st.success(f"正文补全 {result['ok']} 条，失败 {result['failed']} 条。")
            evidence = store.list_evidence(code)
        query = st.text_input("检索已沉淀证据", key=f"ev_query_{code}",
                              placeholder="标题、摘要或来源")
        rows = store.search_evidence(query, symbol=code, limit=80) if query else evidence
        if not rows:
            st.caption("在“个股情报”刷新新闻后，结果会自动进入这里。")
        for row in rows[:50]:
            title = row["title"]
            if row.get("url"):
                title = f"[{title}]({row['url']})"
            st.markdown(f"- {row.get('published') or '时间未知'} · {row.get('source') or row.get('tier')} · "
                        f"质量 {row.get('quality_score', 0)} · {title}")
            with st.expander(f"证据详情 · {row.get('item_kind') or 'first_report'}", expanded=False):
                try:
                    score = json.loads(row.get("score_json") or "{}")
                except Exception:
                    score = {}
                st.json({"推荐原因评分": score, "来源层级": row.get("tier"),
                         "规范化链接": row.get("canonical_url"), "PDF页码": row.get("page_refs"),
                         "正文状态": row.get("fetch_status"),
                         "读取错误": row.get("fetch_error"),
                         "内容哈希": row.get("content_hash")}, expanded=False)
                excerpt = (row.get("full_text") or row.get("snippet") or "")[:1800]
                st.text(excerpt or "（尚未补全正文）")
        latest = store.latest_thesis(code)
        if latest and rows:
            labels = {f"{r['title'][:55]} · {r.get('source') or '未知来源'}": r["evidence_id"]
                      for r in rows[:50]}
            with st.form(f"bind_evidence_{code}"):
                selected = st.multiselect("把证据绑定到当前假设", list(labels),
                                          placeholder="选择一条或多条证据")
                stance = st.radio("证据立场", ("support", "oppose", "pending"),
                                  format_func=lambda x: {"support": "支持", "oppose": "反对",
                                                         "pending": "待确认"}[x], horizontal=True)
                note = st.text_input("绑定说明", placeholder="它支持或反对哪一部分？")
                if st.form_submit_button("保存证据关系", disabled=not selected):
                    services.link_thesis_evidence(latest["id"],
                                                  [labels[x] for x in selected], stance, note)
                    st.success("证据已绑定到假设，并进入关系图。")


def _render_projects(bundle: dict | None) -> None:
    st.markdown(ut.sec_header("研究项目", "一个问题可关联核心标的、同行、供应链和观察标的"),
                unsafe_allow_html=True)
    with st.form("create_research_project"):
        a, b = st.columns([1, 2])
        title = a.text_input("项目名称", placeholder="例如：储能价格周期跟踪")
        due = a.date_input("关注期限", value=date.today() + timedelta(days=90))
        question = b.text_area("研究问题", placeholder="什么事实能证明或推翻当前判断？")
        tags = b.text_input("标签", placeholder="新能源,出海,景气周期")
        if st.form_submit_button("建立研究项目", type="primary"):
            if title.strip():
                services.create_project(title, question, tags, due.isoformat())
                st.success("研究项目已创建。")
                st.rerun()
            else:
                st.warning("请填写项目名称。")
    projects = services.list_projects()
    if not projects:
        st.info("尚无研究项目。项目用于把多只股票、假设、证据和结论组织在一起。")
        return
    for project in projects:
        with st.expander(f"{project['title']} · {project['status']} · {project['stock_count']}只标的",
                         expanded=False):
            st.write(project["question"] or "（未填写研究问题）")
            st.caption(f"标签：{project['tags'] or '—'} · 截止：{project['due_date'] or '—'}")
            stocks = services.project_stocks(project["id"])
            if stocks:
                st.dataframe(pd.DataFrame(stocks)[["symbol", "name", "role", "note"]],
                             hide_index=True, width="stretch")
                remove_map = {f"{x['symbol']} {x['name']} · {x['role']}": x for x in stocks}
                rc1, rc2 = st.columns([3, 1])
                remove_label = rc1.selectbox("项目标的管理", list(remove_map),
                                             key=f"project_stock_manage_{project['id']}")
                if rc2.button("移出项目", key=f"project_stock_remove_{project['id']}",
                              width="stretch"):
                    services.remove_project_stock(project["id"],
                                                  remove_map[remove_label]["symbol"])
                    st.rerun()
            if bundle:
                code, name = bundle["symbol"][2:], bundle["name"]
                c1, c2, c3 = st.columns([1.2, 1.5, 2])
                role = c1.selectbox("角色", ("core", "peer", "supply_chain", "watch"),
                                    format_func=lambda x: {"core": "核心标的", "peer": "同行",
                                                           "supply_chain": "供应链", "watch": "观察"}[x],
                                    key=f"project_role_{project['id']}")
                note = c2.text_input("标的备注", key=f"project_note_{project['id']}")
                if c3.button(f"加入当前标的 {name} {code}", key=f"add_project_{project['id']}",
                             width="stretch"):
                    services.add_project_stock(project["id"], code, name, role, note)
                    st.rerun()
            state = st.selectbox("项目状态", ("active", "paused", "completed", "archived"),
                                 index=("active", "paused", "completed", "archived").index(
                                     project["status"] if project["status"] in
                                     ("active", "paused", "completed", "archived") else "active"),
                                 key=f"project_state_{project['id']}")
            if state != project["status"]:
                services.update_project(project["id"], status=state)
                st.rerun()


def _render_timeline(bundle: dict | None) -> None:
    st.markdown(ut.sec_header("个股事件时间轴", "公告、新闻、财报与价格异动进入同一事件模型"),
                unsafe_allow_html=True)
    if not bundle:
        st.info("请先加载一只股票。")
        return
    code, name, df = bundle["symbol"][2:], bundle["name"], bundle["df_all"]
    research_analytics.ingest_price_events(code, name, df)
    try:
        import state
        financial_snapshot = state.fetch_fundamentals(code)
        research_analytics.ingest_financial_report_events(code, name, financial_snapshot)
    except Exception:
        pass
    events = services.list_events(code, 500)
    types = sorted({x["event_type"] for x in events})
    f1, f2, f3, f4 = st.columns(4)
    selected_types = f1.multiselect("事件类型", types, default=types,
                                    placeholder="默认显示全部")
    source_level = f2.selectbox("来源层级", ("全部", "官方", "媒体", "未证实"))
    direction = f3.selectbox("影响方向", ("全部", "bull", "bear", "neutral"))
    impact = f4.selectbox("影响等级", ("全部", "high", "mid", "low"))
    def source_of(event):
        return ("官方" if event["verified"] == "official" else
                "未证实" if event["verified"] == "unverified" else "媒体")
    shown = [e for e in events if (not selected_types or e["event_type"] in selected_types)
             and (source_level == "全部" or source_of(e) == source_level)
             and (direction == "全部" or e["stance"] == direction)
             and (impact == "全部" or e["impact"] == impact)]
    chart = df.tail(260).copy()
    chart["date"] = pd.to_datetime(chart["date"])
    fig = go.Figure(go.Candlestick(x=chart["date"], open=chart["open"], high=chart["high"],
                                   low=chart["low"], close=chart["close"], name="K线"))
    marker_x, marker_y, marker_text = [], [], []
    dates = chart.set_index(chart["date"].dt.normalize())["close"]
    for e in shown[:120]:
        try:
            day = pd.Timestamp((e["earliest_at"] or e["latest_at"])[:10]).normalize()
            prior = dates.loc[dates.index <= day]
            if not prior.empty:
                marker_x.append(day); marker_y.append(float(prior.iloc[-1]))
                marker_text.append(f"{e['event_type']}｜{e['title']}")
        except Exception:
            continue
    fig.add_trace(go.Scatter(x=marker_x, y=marker_y, mode="markers", name="事件",
                             text=marker_text, hovertemplate="%{text}<extra></extra>",
                             marker={"size": 10, "color": "#f0b429", "symbol": "diamond"}))
    fig.update_layout(height=500, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch")
    c1, c2 = st.columns([1, 3])
    if c1.button("计算事件后收益", type="primary", width="stretch"):
        with st.spinner("按分市场基准计算 1/5/20/60 个交易日收益…"):
            studies = research_analytics.run_event_studies(code, df, shown)
        st.session_state[f"event_studies_{code}"] = studies
    c2.caption("只使用事件发生后的交易日；创业板/科创板/北交所自动匹配指数，缺失回退沪深300。")
    studies = st.session_state.get(f"event_studies_{code}") or []
    if studies:
        table = []
        for x in studies:
            row = {"事件": x["title"], "类型": x["event_type"], "事件日": x["event_trade_date"],
                   "基准": x["benchmark"], "可比性": x["comparability"]}
            for w in research_analytics.WINDOWS:
                row[f"{w}日收益%"] = x["returns"].get(str(w))
                row[f"{w}日超额%"] = x["excess"].get(str(w))
            table.append(row)
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        chosen_kind = st.selectbox("同类事件对照", sorted({x["event_type"] for x in studies}),
                                   key=f"similar_event_type_{code}")
        target = next((x for x in studies if x["event_type"] == chosen_kind), {})
        peers = services.similar_events(chosen_kind, code,
                                        target_event_id=target.get("event_id", ""))
        if peers:
            peer_cols = ["symbol", "name", "title", "latest_at", "source_count",
                         "similarity_score", "similarity_basis", "comparability",
                         "returns_json", "excess_json"]
            st.dataframe(pd.DataFrame(peers)[peer_cols], hide_index=True, width="stretch")
            if st.button("补算同公司/同行事件样本", key=f"peer_study_{code}_{chosen_kind}"):
                with st.spinner("正在按事件日读取同行历史行情并计算超额收益…"):
                    st.session_state[f"peer_studies_{code}_{chosen_kind}"] = (
                        research_analytics.run_peer_event_studies(peers))
            peer_result = st.session_state.get(f"peer_studies_{code}_{chosen_kind}")
            if peer_result:
                st.caption(f"完成 {peer_result['completed']} 个样本 · 失败 {len(peer_result['failed'])} 个")
                if peer_result["results"]:
                    rows = []
                    for item in peer_result["results"]:
                        row = {"代码": item["symbol"], "事件": item["title"],
                               "相似度": item.get("similarity_score"), "基准": item["benchmark"]}
                        for window in research_analytics.WINDOWS:
                            row[f"{window}日超额%"] = item["excess"].get(str(window))
                        rows.append(row)
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if shown:
        st.dataframe(pd.DataFrame(shown)[["latest_at", "event_type", "title", "verified",
                                             "earliest_source", "authority_source", "latest_source",
                                             "source_count", "contradiction", "priced_in"]],
                     hide_index=True, width="stretch")
    else:
        st.info("当前筛选下没有事件；先在“个股情报”刷新新闻即可自动形成事件。")


def _graph_figure(nodes: list[dict], edges: list[dict]) -> go.Figure:
    if not nodes:
        return go.Figure()
    ordered = sorted(nodes, key=lambda x: (x["node_type"], x["label"]))
    pos = {}
    total = len(ordered)
    for i, node in enumerate(ordered):
        angle = 2 * math.pi * i / max(total, 1)
        radius = 1.0 + (hash(node["node_type"]) % 3) * .18
        pos[node["node_id"]] = (radius * math.cos(angle), radius * math.sin(angle))
    ex, ey = [], []
    for edge in edges:
        if edge["source_id"] in pos and edge["target_id"] in pos:
            a, b = pos[edge["source_id"]], pos[edge["target_id"]]
            ex.extend([a[0], b[0], None]); ey.extend([a[1], b[1], None])
    fig = go.Figure(go.Scatter(x=ex, y=ey, mode="lines", hoverinfo="skip",
                               line={"color": "rgba(140,150,170,.35)", "width": 1}))
    colors = {"company": "#3b82f6", "event": "#f59e0b", "source": "#64748b",
              "industry": "#8b5cf6", "person": "#ec4899", "product": "#14b8a6",
              "thesis": "#ef4444", "conclusion": "#22c55e", "metric": "#06b6d4"}
    fig.add_trace(go.Scatter(
        x=[pos[n["node_id"]][0] for n in ordered], y=[pos[n["node_id"]][1] for n in ordered],
        mode="markers+text", text=[n["label"][:18] for n in ordered], textposition="top center",
        customdata=[[n["node_type"], n["confidence"], "已核验" if n["verified"] else "待核验"]
                    for n in ordered],
        hovertemplate="%{text}<br>%{customdata[0]} · 可信度 %{customdata[1]:.0%}<br>%{customdata[2]}<extra></extra>",
        marker={"size": [17 if n["node_type"] in ("company", "thesis", "conclusion") else 11
                           for n in ordered],
                "color": [colors.get(n["node_type"], "#94a3b8") for n in ordered],
                "line": {"width": 1, "color": "white"}}))
    fig.update_layout(height=600, showlegend=False, xaxis={"visible": False}, yaxis={"visible": False},
                      margin=dict(l=5, r=5, t=10, b=5))
    return fig


def _render_graph(bundle: dict | None) -> None:
    st.markdown(ut.sec_header("证据关系图", "从结论反向追溯到假设、事件、来源和原文"),
                unsafe_allow_html=True)
    code = bundle["symbol"][2:] if bundle else ""
    projects = services.list_projects()
    project_map = {"当前标的": None, **{p["title"]: p["id"] for p in projects}}
    selected_scope = st.selectbox("图谱范围", list(project_map), key="graph_scope")
    min_conf = st.slider("最低关系可信度", 0.0, 1.0, .45, .05)
    project_id = project_map[selected_scope]
    nodes, edges = services.graph_data("" if project_id else code, project_id=project_id,
                                       min_confidence=min_conf)
    if not nodes:
        st.info("暂无图谱关系。刷新个股情报或完成一次 AI 研究后会自动建立。")
        return
    st.plotly_chart(_graph_figure(nodes, edges), width="stretch")
    type_options = sorted({n["node_type"] for n in nodes})
    chosen_types = st.multiselect("节点类型", type_options, default=type_options)
    filtered = [n for n in nodes if n["node_type"] in chosen_types]
    st.dataframe(pd.DataFrame(filtered)[["node_type", "label", "confidence", "verified"]],
                 hide_index=True, width="stretch")
    labels = {f"{n['node_type']}｜{n['label']}": n for n in filtered}
    if labels:
        selected = st.selectbox("查看节点原始关系", list(labels))
        node = labels[selected]
        linked = [e for e in edges if node["node_id"] in (e["source_id"], e["target_id"])]
        st.json({"节点": node, "关系": linked}, expanded=False)
        evidence_ids = sorted({e["evidence_id"] for e in linked if e.get("evidence_id")})
        if evidence_ids:
            with store.connection() as conn:
                qs = ",".join("?" * len(evidence_ids))
                original = [dict(r) for r in conn.execute(
                    f"SELECT evidence_id,title,source,published,url,page_refs,snippet FROM evidence "
                    f"WHERE evidence_id IN ({qs})", evidence_ids).fetchall()]
            st.markdown("**原始证据与来源**")
            st.dataframe(pd.DataFrame(original), hide_index=True, width="stretch",
                         column_config={"url": st.column_config.LinkColumn("原文")})


def _render_alerts(bundle: dict | None) -> None:
    st.markdown(ut.sec_header("研究预警", "页面关闭后可用 monitor.py 独立持续扫描"),
                unsafe_allow_html=True)
    if bundle:
        code, name = bundle["symbol"][2:], bundle["name"]
        with st.form(f"alert_create_{code}"):
            c1, c2, c3 = st.columns(3)
            typ = c1.selectbox("预警类型", list(monitor_service.RULE_LABELS),
                               format_func=lambda x: monitor_service.RULE_LABELS[x])
            operator = c2.selectbox("比较符", (">=", "<=", ">", "<", "contains"))
            threshold = c3.text_input("阈值/关键词",
                                      placeholder="价格100；关键词减持；财务roe>=15")
            severity = c1.selectbox("严重度", ("low", "medium", "high", "critical"))
            cooldown = c2.number_input("冷却分钟", min_value=1, value=60)
            if st.form_submit_button("创建预警规则", type="primary"):
                monitor_service.create_rule(code, name, typ, threshold, operator,
                                            severity, int(cooldown))
                st.success("预警规则已创建。")
                st.rerun()
    c1, c2 = st.columns([1, 3])
    if c1.button("立即扫描全部规则", width="stretch"):
        with st.spinner("正在检查行情、技术、财务、新闻和假设复核条件…"):
            st.session_state["monitor_result"] = monitor_service.scan_rules(force=True)
    c2.code("python monitor.py --interval 300", language="bash")
    if st.session_state.get("monitor_result"):
        st.write(st.session_state["monitor_result"])
    rules = monitor_service.list_rules(bundle["symbol"][2:] if bundle else "")
    if rules:
        st.dataframe(pd.DataFrame(rules)[["id", "symbol", "name", "rule_type", "operator",
                                             "threshold", "severity", "enabled", "last_triggered"]],
                     hide_index=True, width="stretch")
        rule_map = {f"#{r['id']} {r['name'] or r['symbol']} · {monitor_service.RULE_LABELS.get(r['rule_type'], r['rule_type'])}": r
                    for r in rules}
        selected_rule = rule_map[st.selectbox("编辑规则", list(rule_map), key="edit_alert_rule")]
        ec1, ec2, ec3 = st.columns([2, 1, 1])
        new_threshold = ec1.text_input("新阈值", value=selected_rule["threshold"],
                                       key=f"alert_threshold_{selected_rule['id']}")
        enabled = ec2.checkbox("启用", value=bool(selected_rule["enabled"]),
                               key=f"alert_enabled_{selected_rule['id']}")
        if ec3.button("保存规则", key=f"save_alert_{selected_rule['id']}", width="stretch"):
            monitor_service.update_rule(selected_rule["id"], enabled=enabled,
                                        threshold=new_threshold)
            st.rerun()
    status = st.radio("通知状态", ("unread", "acknowledged", "ignored", "resolved", "全部"),
                      horizontal=True)
    notes = monitor_service.list_notifications("" if status == "全部" else status)
    for note in notes[:50]:
        cols = st.columns([4, 1, 1, 1])
        cols[0].write(f"**{note['title']}** · {note['message']} · {note['triggered_at']}")
        for col, target, label in zip(cols[1:], ("acknowledged", "ignored", "resolved"),
                                      ("确认", "忽略", "解决")):
            if col.button(label, key=f"notification_{target}_{note['id']}"):
                monitor_service.set_notification_status(note["id"], target)
                st.rerun()


def _render_review(bundle: dict | None) -> None:
    st.markdown(ut.sec_header("结论复盘", "保留当时的问题、模型、证据、价格和可证伪条件"),
                unsafe_allow_html=True)
    code = bundle["symbol"][2:] if bundle else ""
    tasks = services.list_research_tasks(code, limit=100)
    if tasks:
        st.markdown("#### 研究任务流水线")
        for task in tasks[:30]:
            with st.expander(
                    f"任务 #{task['id']} · {task['status']} · 当前阶段 {task['current_stage']} · "
                    f"覆盖率 {task['coverage']:.0f}%"):
                try:
                    subquestions = json.loads(task["subquestions_json"] or "[]")
                    sub_status = json.loads(task.get("subquestion_status_json") or "[]")
                    stages = json.loads(task["stage_json"] or "{}")
                    unresolved = json.loads(task["unresolved_json"] or "[]")
                except Exception:
                    subquestions, sub_status, stages, unresolved = [], [], {}, []
                stage_rows = [{"阶段": k, "状态": v} for k, v in stages.items()]
                if stage_rows:
                    st.dataframe(pd.DataFrame(stage_rows), hide_index=True, width="stretch")
                status_labels = {"complete": "✅", "running": "⏳", "pending": "○",
                                 "blocked": "⛔", "needs_review": "⚠️"}
                if sub_status:
                    st.markdown("**子问题**\n\n" + "\n".join(
                        f"{status_labels.get(item.get('status'), '○')} {i}. {item.get('question', '')}"
                        for i, item in enumerate(sub_status, 1)))
                else:
                    st.markdown("**子问题**\n\n" + "\n".join(
                        f"{i}. {q}" for i, q in enumerate(subquestions, 1)))
                st.caption(
                    f"证据 {task['evidence_count']} 条 · 冲突 {task['conflict_count']} 个 · "
                    f"未解决 {len(unresolved)} 项 · 更新时间 {task['updated_at']}")
                if task["error"]:
                    st.error(task["error"])
    runs = services.list_research_runs(code, limit=100)
    if not runs:
        st.info("尚无归档结论。在 AI 研究助手完成一次任务后会自动保存。")
    for run in runs:
        mark = {"complete": "✅", "incomplete": "⚠️"}.get(run["status"], "•")
        with st.expander(f"{mark} #{run['id']} {run['created_at']} · {run['task_type']} · 覆盖率 {run['coverage']:.0f}%"):
            st.markdown(run["answer"] or "（无正文）")
            try:
                structured = json.loads(run["structured_json"])
            except Exception:
                structured = {}
            st.json({"结构化结论": structured, "基准价格": run["baseline_price"],
                     "有效期": run["valid_until"], "当前复盘": run["outcome"] or "待复盘"},
                    expanded=False)
            with st.form(f"review_run_{run['id']}"):
                outcome = st.radio("结果", ("correct", "partial", "wrong", "inconclusive"),
                                   format_func=lambda x: {"correct": "正确", "partial": "部分正确",
                                                          "wrong": "错误", "inconclusive": "无法判断"}[x],
                                   horizontal=True)
                note = st.text_area("复盘原因", value=run["outcome_note"] or "")
                if st.form_submit_button("保存复盘"):
                    services.review_run(run["id"], outcome, note)
                    st.rerun()
    if bundle:
        snaps = store.list_snapshots(code, 30)
        if len(snaps) >= 2:
            st.markdown("#### 研究快照对比")
            mapping = {f"#{x['id']} {x['created_at']}": x for x in snaps}
            c1, c2 = st.columns(2)
            left = mapping[c1.selectbox("时间点 A", list(mapping), index=min(1, len(mapping)-1))]
            right = mapping[c2.selectbox("时间点 B", list(mapping), index=0)]
            fields = ("price", "chg_pct", "payload_json", "fundamentals_json", "news_json", "thesis_json")
            st.dataframe(pd.DataFrame([{"字段": f, "A": left.get(f), "B": right.get(f)} for f in fields]),
                         hide_index=True, width="stretch")


def _render_portfolio(bundle: dict | None) -> None:
    st.markdown(ut.sec_header("组合风险", "手动维护或 CSV 导入；不连接券商、不执行交易"),
                unsafe_allow_html=True)
    with st.form("portfolio_create"):
        c1, c2 = st.columns(2)
        pname = c1.text_input("新组合名称")
        pnote = c2.text_input("说明")
        if st.form_submit_button("创建组合", disabled=not pname.strip()):
            services.create_portfolio(pname, pnote)
            st.rerun()
    portfolios = services.list_portfolios()
    if not portfolios:
        st.info("先创建一个组合。")
        return
    labels = {f"{p['name']}（{p['position_count']}只）": p for p in portfolios}
    portfolio = labels[st.selectbox("当前组合", list(labels))]
    with st.form(f"position_{portfolio['id']}"):
        cols = st.columns(6)
        default_code = bundle["symbol"][2:] if bundle else ""
        default_name = bundle["name"] if bundle else ""
        symbol = cols[0].text_input("代码", value=default_code)
        name = cols[1].text_input("名称", value=default_name)
        shares = cols[2].number_input("数量", min_value=0.0, value=0.0)
        cost = cols[3].number_input("成本价", min_value=0.0, value=0.0)
        industry = cols[4].text_input("行业")
        tags = cols[5].text_input("标签")
        if st.form_submit_button("新增/更新持仓"):
            services.upsert_position(portfolio["id"], symbol, name, shares, cost, industry, tags)
            st.rerun()
    uploaded = st.file_uploader("导入 CSV（代码、名称、数量、成本价、行业、标签）", type="csv",
                                key=f"portfolio_csv_{portfolio['id']}")
    if uploaded and st.button("确认导入", key=f"portfolio_import_{portfolio['id']}"):
        try:
            frame = pd.read_csv(uploaded, dtype={"代码": str, "symbol": str})
            ok, errors = services.import_positions(portfolio["id"], frame.to_dict("records"))
            st.success(f"导入 {ok} 行。")
            if errors:
                st.warning("；".join(errors[:10]))
            st.rerun()
        except Exception as e:
            st.error(f"CSV 解析失败：{e}")
    positions = services.list_positions(portfolio["id"])
    export_df = pd.DataFrame(services.portfolio_csv_rows(portfolio["id"]))
    st.download_button("导出组合 CSV", export_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{portfolio['name']}_positions.csv", mime="text/csv")
    if not positions:
        st.info("组合暂无持仓。")
        return
    position_map = {f"{p['symbol']} {p['name']}": p for p in positions}
    dc1, dc2 = st.columns([3, 1])
    delete_label = dc1.selectbox("持仓管理", list(position_map),
                                 key=f"position_manage_{portfolio['id']}")
    if dc2.button("删除所选持仓", key=f"position_delete_{portfolio['id']}", width="stretch"):
        services.delete_position(position_map[delete_label]["id"])
        st.rerun()
    if st.button("刷新组合风险", type="primary", key=f"portfolio_analyze_{portfolio['id']}"):
        histories, market_caps, failures = {}, {}, []
        progress = st.progress(0, text="正在读取 120 日行情…")
        for i, pos in enumerate(positions):
            try:
                histories[pos["symbol"]] = dtf.get_history(
                    dtf.normalize_symbol(pos["symbol"]), "daily", "qfq")
                import state
                import fundamentals as fund
                market_caps[pos["symbol"]] = fund._pick_val(
                    state.fetch_spot_extra(pos["symbol"]), "总市值")
            except Exception as e:
                failures.append(f"{pos['symbol']}:{type(e).__name__}")
            progress.progress((i + 1) / len(positions), text=f"已处理 {i + 1}/{len(positions)}")
        st.session_state[f"portfolio_risk_{portfolio['id']}"] = {
            "analysis": research_analytics.analyze_portfolio(
                positions, histories, market_caps=market_caps),
            "failures": failures}
        progress.empty()
    payload = st.session_state.get(f"portfolio_risk_{portfolio['id']}")
    if not payload:
        st.dataframe(pd.DataFrame(positions), hide_index=True, width="stretch")
        return
    risk = payload["analysis"]
    a, b, c = st.columns(3)
    a.metric("持仓市值", f"{risk['total_value']:,.0f}")
    b.metric("HHI集中度", f"{risk['concentration']:.3f}")
    c.metric("数据失败", len(payload["failures"]))
    for warning in risk["warnings"]:
        st.warning(warning)
    st.dataframe(pd.DataFrame(risk["rows"]), hide_index=True, width="stretch")
    style = risk.get("style_exposure") or {}
    if style:
        st.markdown("#### 风格暴露代理")
        style_rows = [
            {"因子": "大盘/小盘", "正向权重%": style.get("large_cap_weight", 0),
             "反向权重%": style.get("small_cap_weight", 0)},
            {"因子": "正/负60日动量", "正向权重%": style.get("positive_momentum_weight", 0),
             "反向权重%": style.get("negative_momentum_weight", 0)},
            {"因子": "高/低波动", "正向权重%": style.get("high_volatility_weight", 0),
             "反向权重%": style.get("low_volatility_weight", 0)},
        ]
        st.dataframe(pd.DataFrame(style_rows), hide_index=True, width="stretch")
    corr = risk["correlation"]
    if not corr.empty:
        st.plotly_chart(go.Figure(go.Heatmap(z=corr.values, x=corr.columns, y=corr.index,
                                             zmin=-1, zmax=1, colorscale="RdBu_r")), width="stretch")
    if risk["industry_weights"]:
        st.bar_chart(pd.Series(risk["industry_weights"], name="权重%"))
    shared = services.portfolio_shared_exposures([x["symbol"] for x in positions])
    if shared:
        st.markdown("#### 供应链与共同风险暴露")
        st.dataframe(pd.DataFrame(shared), hide_index=True, width="stretch")
    symbols = [x["symbol"] for x in positions]
    with store.connection() as conn:
        qs = ",".join("?" * len(symbols))
        impacted = [dict(r) for r in conn.execute(
            f"SELECT symbol,event_type,title,stance,impact,verified,latest_at FROM events "
            f"WHERE symbol IN ({qs}) ORDER BY latest_at DESC LIMIT 100", symbols).fetchall()]
    if impacted:
        st.markdown("#### 组合事件影响")
        weights = {x["symbol"]: x["weight"] for x in risk["rows"]}
        for x in impacted:
            x["组合权重贡献%"] = weights.get(x["symbol"], 0)
        st.dataframe(pd.DataFrame(impacted), hide_index=True, width="stretch")


def render_research_page(bundle: dict | None) -> None:
    info = store.overview()
    st.markdown(ut.sec_header("个人研究库", "自选、假设、证据与快照都保存在本机"),
                unsafe_allow_html=True)
    a, b, c, d, e, f = st.columns(6)
    a.metric("自选", info["watchlist"])
    b.metric("项目", info["projects"])
    c.metric("假设", info["theses"])
    d.metric("证据", info["evidence"])
    e.metric("事件", info["events"])
    f.metric("未读预警", info["alerts"])
    st.caption(f"本机数据库：{store.db_location()} · 不上传云端")
    views = {"overview": "研究档案", "projects": "研究项目", "timeline": "事件时间轴",
             "graph": "证据图谱", "alerts": "预警中心", "review": "历史复盘",
             "portfolio": "组合风险"}
    view = st.segmented_control("研究库模块", list(views), default="overview",
                                format_func=lambda x: views[x], label_visibility="collapsed",
                                width="stretch", key="research_library_view") or "overview"
    if view == "overview":
        if bundle:
            _render_active(bundle)
        else:
            st.info("当前没有已加载标的；可先从下方自选打开，或在左侧输入代码分析。")
        _render_watchlist()
        if bundle:
            code = bundle["symbol"][2:]
            aliases = store.list_entity_aliases(code)
            with st.expander("实体别名与搜索覆盖", expanded=False):
                st.caption("用于搜索公司全称、英文名、品牌、子公司和供应链关键词。")
                new_alias = st.text_input("新增别名", key=f"new_alias_{code}")
                alias_type = st.selectbox("别名类型", ("company_name", "english", "subsidiary",
                                                         "brand", "product", "supply_chain", "custom"),
                                          key=f"alias_type_{code}")
                if st.button("保存别名", key=f"save_alias_{code}", disabled=not new_alias.strip()):
                    store.upsert_entity_alias(code, new_alias, alias_type, True)
                    st.rerun()
                if aliases:
                    st.dataframe(pd.DataFrame(aliases)[["alias", "alias_type", "verified"]],
                                 hide_index=True, width="stretch")
            st.markdown(ut.sec_header("版本与证据", "保留历史，并把正反证据明确绑定到假设"),
                        unsafe_allow_html=True)
            _render_history(code)
            st.download_button("导出个人研究档案", store.export_dossier(code),
                               file_name=f"{code}_research_dossier.md", mime="text/markdown",
                               icon=":material/download:")
    elif view == "projects":
        _render_projects(bundle)
    elif view == "timeline":
        _render_timeline(bundle)
    elif view == "graph":
        _render_graph(bundle)
    elif view == "alerts":
        _render_alerts(bundle)
    elif view == "review":
        _render_review(bundle)
    elif view == "portfolio":
        _render_portfolio(bundle)
