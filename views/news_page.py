"""消息情报页：个股多渠道抓取 + 利好/利空研判 + 题材催化。"""
import os

import streamlit as st

from astock.ui import ui_theme as ut
from astock.research import research_services
from astock.ai.ai_client import load_local_config
from astock.news.news_intel import LOOKBACK_CHOICES, analyze_news, analyze_items, apply_filters, collect_news, item_view, lookback_label, parse_theme_input, status_entries
from astock.search.search_tools import search_health
from views.search_progress import progress_text, render_search_report


def _saved_key() -> str:
    return (load_local_config().get("api_key")
            or os.environ.get("ASTOCK_AI_KEY") or "")


def _run_item_ai(it, ai: dict, api_key: str, context: str):
    analyze_items([it], ai["provider_cfg"], ai["model"], api_key, context=context)


def _persist_intelligence(code6: str, name: str, digest, ai: dict,
                          api_key: str) -> dict:
    """新闻入库后自动形成事件、图谱和冲突记录；AI 不可用时规则降级。"""
    items = [*digest.items, *digest.theme_items]
    result = research_services.process_news_intelligence(
        code6, name, items, provider_cfg=ai.get("provider_cfg") or {},
        model=ai.get("model") or "", api_key=api_key)
    # 正文抓取逐项降级；只补全最重要的少量证据，避免一次刷新长时间阻塞。
    hydrated = research_services.hydrate_evidence_content(code6, limit=5)
    return {**result, "hydrated": hydrated}


def render_news_feed(items, *, key_prefix: str, has_key: bool, ai: dict,
                     api_key: str, context: str):
    """卡片 + 单条 AI 研判。items 须是 session 里的同一批对象。"""
    for it in items:
        view = item_view(it)
        st.markdown(ut.news_item_card(view), unsafe_allow_html=True)
        if view.get("quality_breakdown"):
            with st.expander(f"为什么推荐 · 评分 {view['quality_score']}", expanded=False):
                labels = {"source_authority": "来源权威", "entity_relevance": "实体相关",
                          "event_importance": "事件重要性", "verification": "官方核验",
                          "freshness": "时效", "noise_penalty": "噪声扣分",
                          "social_penalty": "社交来源扣分"}
                rows = [{"评分项": labels.get(k, k), "分值": v}
                        for k, v in view["quality_breakdown"].items()
                        if k not in {"raw_total", "total"}]
                st.dataframe(rows, hide_index=True, width="stretch")
        lab = "🔄 重新 AI 研判" if it.ai_analyzed else "🤖 AI 研判"
        if st.button(lab, key=f"{key_prefix}_{it.uid}", disabled=not has_key):
            try:
                _run_item_ai(it, ai, api_key, context)
            except Exception as e:  # noqa: BLE001
                st.error(f"单条研判失败：{type(e).__name__}: {e}")
            else:
                st.rerun()


def render_news_intel(ai: dict, code6: str, name: str):
    """个股消息情报页。"""
    st.markdown(
        ut.sec_header("个股情报",
                      "先按权威性、时效和相关度排序，再由模型做证据化研判"),
        unsafe_allow_html=True)
    st.markdown(
        '<div class="nnote">情报源覆盖公告、主流财经媒体、研报、全球媒体和社交平台。'
        "系统会合并转载、剔除栏目页，把官方披露和主流媒体的重大事项排在前面。"
        "配置模型后，Agent 会规划查询、阅读原文并核验补搜；没有模型时降级基础检索。"
        "开启自动研判后，还会逐条标注影响并生成综合结论。"
        "社交内容始终标记为未证实，不能与公告等量齐观。"
        "</div>",
        unsafe_allow_html=True)

    api_key = (ai.get("api_key") or _saved_key()).strip()
    has_key = bool(api_key and ai.get("model"))
    extra_raw = st.text_input(
        "补充题材（可空；不自动从新闻用规则提取）",
        key="news_theme",
        placeholder="如材料名、技术节点、政策关键词；多个用逗号分隔")
    extra_themes = parse_theme_input(extra_raw)
    lookback_ui = st.radio(
        "时间范围",
        [lab for _, lab in LOOKBACK_CHOICES],
        index=1,
        horizontal=True,
        key="news_lookback",
        help="Agent-Reach 按所选天数检索并检查来源日期；时间未知的消息仅作待核验线索。")
    lookback_days = next((d for d, lab in LOOKBACK_CHOICES if lab == lookback_ui), 7)
    window_lab = lookback_label(lookback_days)
    theme_key = "-".join(extra_themes) or "none"
    cache_key = f"news_digest:{code6}:{lookback_days}:{theme_key}"
    digest = st.session_state.get(cache_key)
    auto_ai = st.toggle("抓取后自动 AI 研判", value=has_key,
                        disabled=not has_key, key="news_auto_ai",
                        help="关闭后省略最终影响分析；配置了模型时，搜索规划和核验仍会调用模型")
    c1, c2, c3 = st.columns([1.15, 1.15, 2])
    fetch = c1.button("刷新情报", type="primary", key="news_fetch",
                      width="stretch", icon=":material/sync:")
    reai = c2.button("🤖 综合分析全部", key="news_reai",
                     disabled=not (digest and digest.items and has_key),
                     width="stretch")
    c3.caption(f"近{window_lab} · 基础检索无需 Key · Agent 规划和核验使用当前模型")

    if not has_key:
        st.info("当前可正常抓取和排序；如需利好/利空研判，请配置模型。")

    if fetch:
        with st.status(f"正在研究近{window_lab}个股资讯…", expanded=True) as workflow:
            try:
                digest = collect_news(code6, name, extra_themes=extra_themes,
                                      lookback_days=lookback_days, research_config={**ai, "api_key": api_key},
                                      on_progress=lambda event: workflow.write(progress_text(event)))
            except Exception as e:  # noqa: BLE001
                st.error(f"采集失败：{type(e).__name__}: {e}")
                return
            workflow.update(label="研究流程结束（核验状态见下方）", state="complete", expanded=False)
        if digest.items and auto_ai and has_key:
            with st.spinner("AI 正在逐条标注，并把外文标题译成中文…"):
                digest = analyze_news(digest, ai["provider_cfg"],
                                      ai["model"], api_key)
        st.session_state[cache_key] = digest
        with st.spinner("正在沉淀事件、全文索引、证据关系与冲突状态…"):
            intel = _persist_intelligence(code6, name, digest, ai, api_key)
        st.session_state[f"news_intel_status:{code6}"] = intel

    if reai and digest and digest.items:
        if not has_key:
            st.warning("请先在左侧填写 API Key 与模型。")
        else:
            with st.spinner("AI 正在重新研判并核对中文译文…"):
                digest = analyze_news(digest, ai["provider_cfg"],
                                      ai["model"], api_key)
            st.session_state[cache_key] = digest
            intel = _persist_intelligence(code6, name, digest, ai, api_key)
            st.session_state[f"news_intel_status:{code6}"] = intel

    digest = st.session_state.get(cache_key)
    if not digest:
        st.info(f"点击「刷新情报」采集该股近{window_lab}公告与主要新闻；AI 研判可选。")
        return

    intel = st.session_state.get(f"news_intel_status:{code6}") or {}
    render_search_report(digest.search_report)
    render_search_report(digest.theme_search_report, "题材搜索过程与证据缺口")
    if intel:
        hydrated = intel.get("hydrated") or {}
        st.caption(
            f"研究闭环：形成 {intel.get('events', 0)} 个事件簇 · "
            f"沉淀 {intel.get('evidence', 0)} 条证据 · "
            f"正文补全 {hydrated.get('ok', 0)}/{hydrated.get('total', 0)} · "
            f"关系/冲突分析 {'AI+规则' if intel.get('ai_used') else '规则降级'}")

    cnt = digest.counts or {}
    bull, bear, neu = cnt.get("bull", 0), cnt.get("bear", 0), cnt.get("neutral", 0)
    used_days = getattr(digest, "lookback_days", lookback_days)
    hint = (("模型研判 · " if digest.ai_used else "待模型研判 · ")
            + f"抓取于 {digest.fetched_at} · 近{lookback_label(used_days)}共 "
            + f"{cnt.get('total', 0)} 条 "
            + f"（官方 {cnt.get('official', 0)} / 媒体 {cnt.get('media', 0)} / "
            + f"社交 {cnt.get('social', 0)} / 全网 {cnt.get('web', 0)}）")
    st.markdown(ut.news_verdict_banner(digest.verdict or "消息面待观察",
                                       digest.overall, bull, bear, neu, hint),
                unsafe_allow_html=True)
    st.markdown(ut.news_meter(bull, bear, neu), unsafe_allow_html=True)
    st.markdown(ut.news_channel_bar(status_entries(digest.channel_status)),
                unsafe_allow_html=True)
    health = search_health()
    if health:
        with st.expander("搜索覆盖与渠道健康", expanded=False):
            st.caption("这是本次进程最近一次调用状态，可用于判断漏搜来自空结果、限流还是渠道故障。")
            for row in health:
                mark = "✅" if row["ok"] else "⚠️"
                detail = f"最近返回 {row['count']} 条" if row["ok"] else (row["error"] or "空结果")
                latency = f" · {row.get('latency_ms', 0)}ms" if row.get("latency_ms") else ""
                st.write(f"{mark} {row['engine']} · {detail}{latency} · {row['checked_at']}")
                if row.get("last_success_at") or row.get("rate_limited_until"):
                    st.caption(f"最近成功：{row.get('last_success_at') or '—'} · "
                               f"限流恢复：{row.get('rate_limited_until') or '未限流'}")

    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(ut.kpi("采集条数", str(cnt.get("total", 0)), "去重后"),
                unsafe_allow_html=True)
    k2.markdown(ut.kpi("利好", str(bull), cls="pos"), unsafe_allow_html=True)
    k3.markdown(ut.kpi("利空", str(bear), cls="neg"), unsafe_allow_html=True)
    k4.markdown(ut.kpi("社交/未证实", str(cnt.get("social", 0)),
                       "含股吧微博等"), unsafe_allow_html=True)

    if digest.summary:
        st.markdown(ut.sec_header("AI 综述", "基于上方条目，不额外编造未出现的事实"),
                    unsafe_allow_html=True)
        st.markdown(digest.summary)

    if digest.themes or digest.theme_items:
        st.markdown(ut.sec_header("题材催化", "从个股新闻提取并另搜进展，判断对该股利好/利空"),
                    unsafe_allow_html=True)
        if digest.themes:
            st.markdown(ut.theme_pills(digest.themes), unsafe_allow_html=True)
        if digest.theme_summary:
            st.markdown(digest.theme_summary)
        elif digest.theme_items and not digest.ai_used:
            st.caption("题材条目待模型研判。")
        if digest.theme_items:
            render_news_feed(
                digest.theme_items, key_prefix=f"theme_{code6}",
                has_key=has_key, ai=ai, api_key=api_key,
                context=f"A股 {name}（{code6}）题材进展。不先验真伪，判断对该股映射的利好/利空。")

    focus_items = [it for it in digest.items
                   if int(getattr(it, "quality_score", 0) or 0) >= 72][:6]
    if focus_items:
        st.markdown(ut.sec_header("重点新闻", "权威来源、重大事项和最新进展优先"),
                    unsafe_allow_html=True)
        render_news_feed(
            focus_items, key_prefix=f"focus_{code6}", has_key=has_key,
            ai=ai, api_key=api_key,
            context=f"A股 {name}（{code6}）重点消息。必须依据条目，不得补写事实。")

    f1, f2, f3 = st.columns(3)
    tier_opt = f1.radio("来源层级",
                        ("全部", "官方披露", "媒体报道", "社交媒体", "全网检索"),
                        horizontal=True, key="news_tier")
    stance_opt = f2.radio("倾向", ("全部", "利好", "利空", "中性/未标"),
                          horizontal=True, key="news_stance")
    quality_opt = f3.radio("质量", ("全部", "重点/关注"),
                           horizontal=True, key="news_quality")
    tier_map = {"全部": "all", "官方披露": "official", "媒体报道": "media",
                "社交媒体": "social", "全网检索": "web"}
    stance_map = {"全部": "all", "利好": "bull", "利空": "bear", "中性/未标": "neutral"}
    shown = apply_filters(digest.items, tier_map[tier_opt], stance_map[stance_opt])
    if quality_opt == "重点/关注":
        shown = [it for it in shown
                 if int(getattr(it, "quality_score", 0) or 0) >= 72]
    st.caption(f"当前筛选 {len(shown)} / {len(digest.items)} 条 · 每条可单独 AI 研判，或点上方「重新 AI 研判」综合分析全部")
    if not shown:
        st.info("当前筛选无匹配条目，可改回来源或倾向。")
        return
    render_news_feed(
        shown, key_prefix=f"news_{code6}", has_key=has_key,
        ai=ai, api_key=api_key,
        context=f"A股 {name}（{code6}）消息面。社交与传闻保留并标未证实，只判断利好/利空。")
