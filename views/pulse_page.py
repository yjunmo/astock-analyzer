"""全科新闻页：先选板块，再看该板块全球要闻 + X/微博博主评价。"""
import os

import streamlit as st

import ui_theme as ut
from ai_client import load_local_config
from event_pulse import (analyze_pulse, collect_pulse, visible_chains,
                         visible_events, visible_items)
from news_intel import (LOOKBACK_CHOICES, channel_tier, lookback_label,
                        status_entries)
from news_sectors import SECTORS, labels_of
from views.news_page import render_news_feed
from views.search_progress import progress_text, render_search_report


def render_event_pulse(ai: dict):
    """全球要闻页。"""
    st.markdown(
        ut.sec_header("全球要闻",
                      "广泛收集信息 · 产业链深挖 · A股研究推荐"),
        unsafe_allow_html=True)
    st.markdown(
        '<div class="nnote">研究 Agent 先拆解所选板块的问题，检索重要事件并阅读原文，'
        "覆盖新华社、彭博、路透及其他财经媒体；保留报道、快讯、讨论与传闻，不以证实与否筛掉信息。"
        "搜集后继续补搜产业链和公司业务，按上中下游、替代与配套路径推荐3—5支A股研究标的。"
        "不选择板块时研究全市场。范围与预算内保留全部不同链接报道，不代表穷尽全网或能读取付费全文。"
        "推荐是新闻情景下的研究方向，非收益保证，不自动交易。</div>",
        unsafe_allow_html=True)

    lookback_ui = st.radio(
        "时间范围",
        [lab for _, lab in LOOKBACK_CHOICES],
        index=1,
        horizontal=True,
        key="pulse_lookback",
        help="Agent-Reach 按所选天数检索并检查来源日期；未知日期的要闻仅作待核验线索。")
    lookback_days = next((d for d, lab in LOOKBACK_CHOICES if lab == lookback_ui), 7)
    window_lab = lookback_label(lookback_days)
    saved_key = (load_local_config().get("api_key")
                 or os.environ.get("ASTOCK_AI_KEY") or "")
    api_key = (ai.get("api_key") or saved_key).strip()
    has_key = bool(api_key and ai.get("model"))
    view = list(st.session_state.get("pulse_multi") or [])
    focus_seed = str(st.session_state.get("pulse_focus") or "")
    scope_key = "-".join(view) or "all"
    cache_key = f"event_pulse_v2:{lookback_days}:{scope_key}:{focus_seed[:30]}"
    pulse = st.session_state.get(cache_key)

    counts = pulse.sector_counts if pulse else {}
    cat_rows = [
        {"id": sid, "label": spec["label"], "hint": spec["hint"],
         "count": counts.get(sid, 0) if pulse else None}
        for sid, spec in SECTORS.items()
    ]
    st.markdown(ut.sec_header("板块分类", "可不选：默认抓取全市场重大要闻"),
                unsafe_allow_html=True)
    st.markdown(ut.sector_catalog(cat_rows, view), unsafe_allow_html=True)

    cols = st.columns(4)
    for i, (sid, spec) in enumerate(SECTORS.items()):
        with cols[i % 4]:
            mark = " · 已选" if sid in view else ""
            n = counts.get(sid, 0) if pulse else None
            lab = spec["label"] + (f" ({n})" if n is not None else "") + mark
            if st.button(lab, key=f"pulse_cat_{sid}", width="stretch",
                         help=spec["hint"]):
                st.session_state["pulse_multi"] = [sid]
                st.rerun()

    picked = st.multiselect(
        "也可多选板块",
        options=list(SECTORS.keys()),
        format_func=lambda k: SECTORS[k]["label"],
        key="pulse_multi",
    )
    view = list(picked)

    focus = st.text_input(
        "补充关键词（可空）",
        key="pulse_focus",
        placeholder="如 央行决议、OPEC、非农；不要只盯一家公司")
    scope_key = "-".join(view) or "all"
    cache_key = f"event_pulse_v2:{lookback_days}:{scope_key}:{focus[:30]}"
    pulse = st.session_state.get(cache_key)
    auto_ai = st.toggle("搜完自动深挖产业链并推荐 A 股", value=has_key,
                        disabled=not has_key, key="pulse_auto_recommend",
                        help="默认开启。搜索后继续补搜产业和公司业务，生成3—5支A股研究推荐；关闭后只收集信息。模型调用会消耗额度")
    c1, c2, c3 = st.columns([1.15, 1.15, 2])
    fetch = c1.button("刷新要闻", type="primary", key="pulse_fetch",
                      width="stretch", icon=":material/sync:")
    reai = c2.button("🤖 产业链与A股推荐", key="pulse_reai",
                     disabled=not (pulse and pulse.items and has_key),
                     width="stretch")
    c3.caption(f"近{window_lab} · 当前范围："
               + ("、".join(labels_of(view)) if view else "全市场"))

    if not has_key:
        st.info("当前可正常抓取与排序；配置模型后可生成事件综述和相关标的映射。")

    if fetch:
        with st.status(f"正在研究近{window_lab}要闻…", expanded=True) as workflow:
            try:
                pulse = collect_pulse(extra_query=focus, translate_en=True,
                                      sectors=view or None,
                                      lookback_days=lookback_days, research_config={**ai, "api_key": api_key},
                                      on_progress=lambda event: workflow.write(progress_text(event)))
            except Exception as e:  # noqa: BLE001
                st.error(f"采集失败：{type(e).__name__}: {e}")
                return
            workflow.update(label="要闻收集结束", state="complete", expanded=False)
        if pulse.items and auto_ai and has_key:
            pulse.selected = list(view)
            with st.status("AI 正在深挖产业链与A股公司…", expanded=True) as chain_workflow:
                pulse = analyze_pulse(pulse, ai["provider_cfg"],
                                      ai["model"], api_key,
                                      on_progress=lambda event: chain_workflow.write(progress_text(event)))
                chain_workflow.update(label="产业链研究结束", state="complete", expanded=False)
        st.session_state[cache_key] = pulse
        st.rerun()

    if reai and pulse and pulse.items:
        if not has_key:
            st.warning("请先在左侧填写 API Key 与模型。")
        else:
            pulse.selected = list(view)
            with st.status("AI 正在深挖全部消息与产业链…", expanded=True) as chain_workflow:
                pulse = analyze_pulse(pulse, ai["provider_cfg"],
                                      ai["model"], api_key,
                                      on_progress=lambda event: chain_workflow.write(progress_text(event)))
                chain_workflow.update(label="产业链研究结束", state="complete", expanded=False)
            st.session_state[cache_key] = pulse
            st.rerun()

    pulse = st.session_state.get(cache_key)
    if not pulse:
        scope = "、".join(labels_of(view)) if view else "全市场"
        st.info(f"当前范围：{scope}。点击「刷新要闻」采集近{window_lab}主要事件。")
        return

    shown = visible_items(pulse, view)
    render_search_report(pulse.search_report, "检索过程、媒体覆盖与阅读状态")
    src_opt = st.radio("来源", ("全部", "媒体快讯", "社交博主"),
                       horizontal=True, key="pulse_src")
    if src_opt == "社交博主":
        shown = [it for it in shown if channel_tier(it.channel) == "social"]
    elif src_opt == "媒体快讯":
        shown = [it for it in shown if channel_tier(it.channel) != "social"]
    n_ai = sum(1 for it in shown if it.ai_analyzed)
    used_days = getattr(pulse, "lookback_days", lookback_days)
    hint = (("模型研判 · " if pulse.ai_used else "待模型研判 · ")
            + f"抓取于 {pulse.fetched_at} · 近{lookback_label(used_days)} · "
            + ("、".join(labels_of(view)) if view else "全市场")
            + f" · {len(shown)} 条 · 已走模型 {n_ai}")
    st.markdown(ut.news_verdict_banner(pulse.headline or "板块要闻",
                                       pulse.overall, 0, 0, len(shown), hint),
                unsafe_allow_html=True)
    st.markdown(ut.news_channel_bar(status_entries(pulse.channel_status)),
                unsafe_allow_html=True)
    st.caption("✓ 表示此次获得该渠道条目，不代表订阅其全部新闻或读完全部正文；空结果/失败会单独记录。新华社、彭博、路透设有定向检索。")

    recommendations = getattr(pulse, "recommendations", []) if pulse.ai_used else []
    if recommendations:
        st.markdown(ut.sec_header("A股研究推荐", "按新闻情景下相对受益程度排序 · 不是上涨概率"), unsafe_allow_html=True)
        st.dataframe([{"股票": x["name"], "代码": x["code"], "新闻主线": x["event"],
                       "产业环节": x["role"], "产品/业务": x["material"],
                       "受益度": f"{x['bull_score']}/10", "推荐理由": x.get("reason") or x["basis"],
                       "证券标识": "行情名称匹配" if x.get("code_status") == "matched" else "待校对"}
                      for x in recommendations], hide_index=True, width="stretch")
        if pulse.recommendation_status == "draft":
            st.caption("产业链精化或补搜未完成，当前保留的是首轮模型推演。")
        for row in recommendations:
            with st.expander(f"{row['name']} {row['code']} · 推荐路径、催化与风险"):
                st.caption(row.get("business_status") or "业务映射推演")
                st.write("推荐逻辑：" + (row.get("reason") or row["basis"]))
                st.write("下一催化：" + (row.get("catalyst") or "模型未给出具体催化节点"))
                st.write("风险：" + (row.get("risk") or "；".join(row.get("chain_risks", [])) or "产业传导或业务兑现不及预期"))
                if row.get("code_status") != "matched":
                    st.caption("证券代码格式符合A股，当前名称接口未返回，名称/代码待校对。")
                for source in row.get("business_sources", []):
                    st.markdown(f"业务资料：[{source['title']}]({source['url']})")
                    st.write(source["quote"])
    elif pulse.ai_used:
        st.info("当前模型尚未形成可展示的A股候选；新闻仍全部保留，可重新运行产业链研究。")
    if getattr(pulse, "chain_search_report", None):
        render_search_report(pulse.chain_search_report, "产业链与公司业务补搜过程")
        if pulse.chain_search_report.get("limitations"):
            with st.expander("产业研究覆盖说明", expanded=False):
                for note in pulse.chain_search_report["limitations"]:
                    st.write(note)

    if pulse.summary:
        st.markdown(ut.sec_header("事件综述", "全部由模型根据条目归纳，非关键词规则"),
                    unsafe_allow_html=True)
        st.markdown(pulse.summary)

    major = sorted(
        (it for it in shown
         if int(getattr(it, "quality_score", 0) or 0) >= 92),
        key=lambda it: -int(getattr(it, "quality_score", 0) or 0),
    )[:6]
    if major:
        st.markdown(ut.sec_header("重大新闻", "来源权威性、时效与事件影响综合排序"),
                    unsafe_allow_html=True)
        render_news_feed(major, key_prefix="pulse_major", has_key=has_key,
                         ai=ai, api_key=api_key,
                         context="全球重大新闻。必须依据条目事实，区分事实与推断。")

    chains = visible_chains(pulse, view) if pulse.ai_used else []
    if chains:
        st.markdown(ut.sec_header("新闻背后的产业链",
                                  "上游 → 中游 → 下游/配套/替代 · 受益路径与A股映射"),
                    unsafe_allow_html=True)
        html = "".join(ut.tech_chain_card(ch) for ch in chains)
        st.markdown(html, unsafe_allow_html=True)
        for chain in chains:
            if chain.get("chain"):
                with st.expander(chain["event"] + " · 分层传导路径"):
                    st.dataframe(chain["chain"], hide_index=True, width="stretch")
                    if chain.get("risks"):
                        st.write("链条风险：" + "；".join(chain["risks"]))

    evs = visible_events(pulse, view) if pulse.ai_used else []
    if evs:
        st.markdown(ut.sec_header("重大事件", "利好/利空对象"),
                    unsafe_allow_html=True)
        html = "".join(ut.event_card(ev) for ev in evs)
        st.markdown(html, unsafe_allow_html=True)

    st.markdown(ut.sec_header("全部已收集消息", "不同来源与观点均保留 · 来源筛选可选 · 不以真假筛除"),
                unsafe_allow_html=True)
    if not shown:
        st.info("该筛选暂无匹配要闻。可改选板块/来源，或再点「抓取要闻」。")
        return
    st.caption("列表保留当前范围内搜到的全部不同链接报道与观点，仅清除重复链接和导航页；质量分只用于排序。每条均可单独分析。")
    ctx = ("全球要闻与 X/微博博主评价，关注："
           + ("、".join(labels_of(view)) if view else "全市场")
           + "。不先验真伪，只判断利好/利空。")
    render_news_feed(shown, key_prefix="pulse", has_key=has_key,
                     ai=ai, api_key=api_key, context=ctx)
