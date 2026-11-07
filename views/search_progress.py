"""研究进度只展示查询、阅读和证据缺口，不展示模型隐藏推理。"""
import streamlit as st


def progress_text(event: dict) -> str:
    stage = event.get("stage")
    if stage == "plan":
        return "规划：" + "；".join(event.get("subquestions") or ["发现事件、阅读原文、核验证据"])
    if stage == "search_start":
        return "检索：" + event.get("query", "")
    if stage == "search":
        return f"检索结果：{event.get('query', '')} · {event.get('status')} · 新来源 {event.get('count', 0)} 条"
    if stage == "read_start":
        return f"阅读原文：{event.get('source_id')} · {event.get('url')}"
    if stage == "select":
        return "选择原文：" + "、".join(event.get("read_ids") or ["按时效、权威性和查询方向选择"])
    if stage == "read":
        return f"原文读取：{event.get('source_id')} · {event.get('status')}"
    if stage == "evaluate":
        if event.get("objective") == "collect":
            return f"第{event.get('round')}轮信息梳理：检查行业与来源覆盖，继续补搜产业线索"
        coverage = event.get('dimension_coverage')
        suffix = f"，问题证据覆盖 {coverage['coverage']:.0f}%" if coverage else ''
        return f"第{event.get('round')}轮核验：{event.get('claims', 0)} 个有原文引用的事实候选，{len(event.get('gaps') or [])} 个证据缺口" + suffix
    if stage == "degraded":
        return event.get("detail", "模型不可用，降级基础检索")
    if stage in {"chain", "recommend"}:
        return event.get("detail", "产业链研究")
    if stage == "finish":
        return f"搜索结束：{event.get('status')} · {event.get('queries', 0)} 次查询 / {event.get('pages', 0)} 次阅读"
    return ""


def render_search_report(report: dict, title="搜索过程与证据缺口") -> None:
    if not report:
        return
    modes = {"agent": "Agent 研究", "basic": "基础检索（未使用模型）", "degraded": "模型失败后降级", "local": "本地资料研究"}
    read_count = sum(d.get("read_status") == "read" for d in report.get("sources", []))
    collecting = report.get("objective") == "collect"
    state = ("信息收集结束" if report.get("status") == "collected" else "已收集部分信息，可继续分析") if collecting else (
        "证据充分性检查通过" if report.get("status") == "complete" else "仍有待核验内容")
    st.caption(f"{modes.get(report.get('mode'), report.get('mode', ''))} · "
               f"{report.get('queries_used', 0)} 次查询 · 成功阅读 {read_count} 个来源 · "
               + state)
    with st.expander(title, expanded=False):
        for event in report.get("trace") or []:
            text = progress_text(event)
            if text:
                st.write(text)
        for gap in report.get("gaps") or []:
            st.warning(("检索覆盖/阅读限制：" if collecting else "证据缺口：") + str(gap))
        for conflict in report.get("conflicts") or []:
            st.warning("来源冲突：" + str(conflict))
        for answer in report.get('dimension_answers') or []:
            st.write(f"{answer['dimension_id']} · {answer['status']} · {answer['answer']}")
        docs = [{"来源": d["source_id"], "标题": d["title"], "日期": d["published"] or "未知",
                 "原文": d["read_status"], "时效": d.get("time_status", "unknown"), "链接": d["url"]}
                for d in report.get("sources") or [] if d.get("relevant") is not False]
        if docs:
            st.dataframe(docs, hide_index=True, width="stretch",
                         column_config={"链接": st.column_config.LinkColumn("链接")})


def render_analysis_quality(quality: dict) -> None:
    if not quality:
        return
    labels = {d['id']: d['label'] for d in quality.get('plan', {}).get('dimensions', [])}
    statuses = {'supported': '已有实质分析及证据', 'partial': '部分支持', 'missing': '证据不足'}
    st.caption(f"研究问题证据覆盖 {quality.get('coverage', 0):.0f}% · "
               + ('审查通过' if quality.get('complete') else '仍有分析缺口')
               + '；该比例不代表上涨概率或判断正确率。')
    rows = [{'研究维度': labels.get(a['dimension_id'], a['dimension_id']),
             '状态': statuses.get(a['status'], a['status']), '分析': a['answer'],
             '证据': '、'.join(a['evidence_ids']), '限制': a.get('limitations', '')}
            for a in quality.get('dimension_answers', [])]
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch')
    for issue in quality.get('issues', []):
        if issue.get('severity') in {'critical', 'high'}:
            st.warning(issue.get('reason') or issue.get('fix'))
