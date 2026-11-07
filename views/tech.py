"""技术分析页：总览 / 多周期对比 / 基本面 / 历史回测 / AI 解读 五个子页。"""
import json
import pandas as pd
import streamlit as st
from astock.research import research_store
from astock.research import research_services

from astock.market import data_fetcher as dtf
from astock.market import fundamentals as fund
from astock.ai import skill_store
from astock.ui import state
from astock.ui import ui_theme as ut
from astock.analysis.analytics import build_snapshot
from astock.analysis.indicators import compute_all
from astock.ai.ai_agent import run_agent
from astock.ai.ai_client import AIError, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE
from astock.ai.ai_context import build_placeholders, build_research_context, parse_frontmatter, render_system_prompt
from astock.analysis.backtest import DEFAULT_FEE_RATE, backtest_text, run_backtest, trades_df
from astock.ui.charts import make_backtest_figure, make_figure, make_fundamental_figure
from astock.analysis.multi_period import build_multi_period, multi_period_text
from astock.news.news_intel import collect_news
from astock.news.news_quality import evidence_text
from astock.analysis.report import BULL, BEAR
from astock.analysis import shortterm
from views.sidebar import render_skill_editor
from views.search_progress import progress_text


# ---------------------------------------------------------------- 小工具

def _fmt_amt(v):
    if v is None:
        return "--"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.0f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f} 万"
    return f"{v:.0f}"


def _fmt_pct(v, signed: bool = False) -> str:
    if v is None or pd.isna(v):
        return "--"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _render_kpi_grid(cards: list, columns: int = 4):
    """最多四列，避免桌面端出现八张又窄又难读的指标卡。"""
    for start in range(0, len(cards), columns):
        row = cards[start:start + columns]
        cols = st.columns(columns)
        for col, (label, value, sub, *rest) in zip(cols, row):
            cls = rest[0] if rest else ""
            col.markdown(ut.kpi(label, value, sub, cls), unsafe_allow_html=True)


def derive_risk(result: dict, s: dict, df_all: pd.DataFrame,
                limit_up: bool, limit_down: bool):
    """风险等级与依据清单：仅引用已计算的事实，不引入新判断。"""
    reasons = []
    if limit_down:
        reasons.append("最新K线收于跌停——卖出可能无法成交，流动性风险")
    if limit_up:
        reasons.append("最新K线收于涨停——买入信号当日不可成交，次日存在高开回落风险")

    bull, bear = s["bull"], s["bear"]
    if bull + bear == 0:
        reasons.append("有效信号组不足，当前方向性判断可靠性较低")
        level = "中"
    else:
        if bear > bull:
            reasons.insert(0, f"指标组投票空方占优（空 {bear} : 多 {bull}）")
            level = "高" if bear >= 3 else "中"
        elif bull > bear:
            reasons.insert(0, f"指标组投票多方占优（多 {bull} : 空 {bear}）")
            level = "低" if bull >= 3 else "中"
        else:
            reasons.insert(0, f"多空组别持平（{bull}:{bear}），方向选择待确认")
            level = "中"
        for kw in ("超买", "跌破布林下轨", "缩量阴跌"):
            for g in result["groups"]:
                for txt, st_ in g["items"]:
                    if kw in txt:
                        reasons.append(f"[{g['name'].split(' ')[0]}] {txt}")
                        break
                else:
                    continue
                break
    reasons = reasons[:6]
    return level, reasons


# ---------------------------------------------------------------- 总览

def render_overview_panel(df_all, df, result, name, symbol, symbol_disp):
    s = result["score"]
    snapshot = build_snapshot(df_all, result)

    if snapshot.get("ok"):
        st.markdown(ut.sec_header(
            "决策仪表盘", "趋势、动量、风险与信号一致度 · 全部来自已收盘 K 线"),
            unsafe_allow_html=True)
        align = snapshot["alignment"]
        ret20 = snapshot["returns"].get(20)
        tone_cls = {"bull": "pos", "bear": "neg"}.get(
            snapshot["trend_tone"], "")
        dashboard = [
            ("趋势结构", snapshot["trend"], snapshot["trend_note"], tone_cls),
            ("信号一致度", f"{align['value']} · {align['direction']}",
             align["note"]),
            ("20周期动量", _fmt_pct(ret20, signed=True),
             "近期价格收益，不代表未来", "pos" if (ret20 or 0) > 0 else "neg"),
            ("20周期年化波动", _fmt_pct(snapshot["annual_vol_20"]),
             "越高越需要控制仓位"),
            ("60周期最大回撤", _fmt_pct(snapshot["max_drawdown_60"], signed=True),
             "按收盘价计算", "neg"),
            ("年度区间位置", _fmt_pct(snapshot["range_position"]),
             snapshot["range_label"]),
            ("ATR / 现价", _fmt_pct(snapshot["atr_pct"]),
             "单周期常态波动参考"),
            ("量比", f"{snapshot['vol_ratio']:.2f}"
             if snapshot["vol_ratio"] is not None else "--",
             "相对前 5 周期均量"),
        ]
        _render_kpi_grid(dashboard)

    ops = ("操作提示：A股 T+1，信号按已收盘K线计，最早下一交易日开盘关注；"
           "空头仅指减仓/回避，不假设可做空。")
    st.markdown(ut.verdict_banner(s["verdict"], s["tone"], s["bull"], s["bear"], ops),
                unsafe_allow_html=True)

    st.markdown(ut.sec_header("关键指标", "技术面派生 + 东财估值快照；'--' 表示数据缺失"),
                unsafe_allow_html=True)
    extras = state.fetch_spot_extra(symbol[2:])
    close = float(df_all["close"].iloc[-1])
    cards = []

    turnover = fund._pick_val(extras, "换手率")
    if turnover is None and "turnover" in df_all.columns:
        t_last = df_all["turnover"].iloc[-1]
        turnover = float(t_last) if pd.notna(t_last) else None
    cards.append(("换手率", f"{turnover:.2f}%" if turnover is not None else "--",
                  "活跃" if (turnover or 0) > 5 else ("温和" if (turnover or 0) > 1.5 else "低迷")))

    vr = df_all["vol_ratio"].iloc[-1]
    vr = float(vr) if pd.notna(vr) else None
    cards.append(("量比", f"{vr:.2f}" if vr is not None else "--",
                  "放量" if (vr or 0) >= 1.2 else ("平量" if (vr or 0) >= 0.8 else "缩量")))

    if "atr" in df_all.columns and pd.notna(df_all["atr"].iloc[-1]):
        atr = float(df_all["atr"].iloc[-1])
        cards.append(("ATR(14)", f"{atr:.2f}", f"占现价 {atr / close * 100:.1f}%"))

    pct = df_all["close"].pct_change().tail(21)
    if len(pct.dropna()) >= 10:
        vol20 = pct.std() * (242 ** 0.5) * 100
        cards.append(("20日年化波动率", f"{vol20:.0f}%",
                      "高于30%注意仓位控制" if vol20 > 30 else ""))

    win = min(len(df_all), 250)
    hi = float(df_all["close"].tail(win).max())
    dd = (close / hi - 1) * 100
    cls = "neg" if dd < -15 else ""
    cards.append((f"{win}日高点回撤", f"{dd:+.1f}%", f"阶段高点 {hi:.2f}", cls))

    pe = fund._pick_val(extras, "市盈率")
    pb = fund._pick_val(extras, "市净率")
    mcap = fund._pick_val(extras, "总市值")
    cards.append(("市盈率(动)", f"{pe:.1f}" if pe is not None else "--", ""))
    cards.append(("市净率", f"{pb:.2f}" if pb is not None else "--", ""))
    cards.append(("总市值", _fmt_amt(mcap) if mcap is not None else "--", ""))

    _render_kpi_grid(cards)
    if not extras:
        st.caption("估值/换手快照暂不可用（东财接口波动，正在自动重试兜底源），"
                   "其余指标不受影响。")

    st.markdown(ut.sec_header("技术图表", "K线/均线/布林 · 成交量 · MACD · KDJ · RSI"),
                unsafe_allow_html=True)
    st.plotly_chart(make_figure(df), width="stretch")

    plan = result.get("plan")
    left, right = st.columns([1.35, 1])
    with left:
        st.markdown(ut.sec_header("规则信号明细", "五大指标组 · 每组一票 · 避免同源重复计票"),
                    unsafe_allow_html=True)
        chips = "".join(
            ut.grp_chip(g["name"].split(" ")[0],
                        sum(1 for _, t in g["items"] if t == BULL),
                        sum(1 for _, t in g["items"] if t == BEAR))
            for g in result["groups"])
        st.markdown('<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">'
                    + chips + "</div>", unsafe_allow_html=True)
        for g in result["groups"]:
            st.markdown(ut.sig_group(g["name"], g["items"]), unsafe_allow_html=True)
    with right:
        limit_up = bool(df_all["is_limit_up"].iloc[-1]) if "is_limit_up" in df_all.columns else False
        limit_dn = bool(df_all["is_limit_down"].iloc[-1]) if "is_limit_down" in df_all.columns else False
        level, reasons = derive_risk(result, s, df_all, limit_up, limit_dn)
        st.markdown(ut.risk_panel(level, reasons), unsafe_allow_html=True)

        if plan and plan.get("cards"):
            st.markdown(ut.sec_header("价位参考", "近端支撑压力 + ATR 缓冲推算"),
                        unsafe_allow_html=True)
            pc = st.columns(min(len(plan["cards"]), 3))
            for i, (label, value) in enumerate(plan["cards"]):
                pc[i % len(pc)].markdown(
                    ut.kpi(label, value, cls="pxcard"), unsafe_allow_html=True)
            st.caption(plan["note"])

        st.markdown(ut.sec_header("近期交叉事件", "近15个周期"), unsafe_allow_html=True)
        if result["events"].empty:
            st.info("近15个周期内无均线/MACD/KDJ交叉信号", icon="ℹ️")
        else:
            st.dataframe(result["events"], hide_index=True, height=220)


# ---------------------------------------------------------------- 短线（供 AI 注入）

def get_or_build_shortterm(data_key: str, df_all) -> dict:
    key = f"st_{data_key}"
    if key not in st.session_state:
        with st.spinner("统计涨停基因与打板历史…"):
            st.session_state[key] = shortterm.build_shortterm_report(df_all)
    return st.session_state[key]


# ---------------------------------------------------------------- 多周期对比

def get_or_build_multi(data_key: str, df_all, symbol: str, name: str) -> dict:
    key = f"mp_{data_key}"
    if key not in st.session_state:
        with st.spinner("派生日/周/月三周期并计算信号…"):
            st.session_state[key] = build_multi_period(df_all, symbol=symbol,
                                                       name=name)
    return st.session_state[key]


def render_multi_period_panel(df_all, symbol, name, data_key):
    st.markdown(
        ut.sec_header("多周期对比", "同一份日线派生周/月线 · 各自独立计分 · 共振判断"),
        unsafe_allow_html=True)
    mp = get_or_build_multi(data_key, df_all, symbol, name)
    st.markdown(ut.multi_period_cards(mp["entries"], mp["verdict"]),
                unsafe_allow_html=True)
    rows = []
    for p in ("daily", "weekly", "monthly"):
        e = mp["entries"].get(p) or {}
        if e.get("ok"):
            rows.append({"周期": e["label"], "结论": e["verdict"],
                         "多": e["bull"], "空": e["bear"],
                         "周期数": e["n_bars"], "最新收盘": round(e["close"], 2)})
        else:
            rows.append({"周期": e.get("label", p), "结论": "不可用",
                         "多": "--", "空": "--", "周期数": "--", "最新收盘": "--"})
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption("月线需要更长历史，上市较短的新股可能样本不足而标注不可用；"
               "多周期分歧时以大周期趋势为锚。数据为历史K线计算，不构成投资建议。")


# ---------------------------------------------------------------- 基本面

def render_fundamentals_panel(code6: str, name: str):
    st.markdown(
        ut.sec_header("基本面分析", "财务指标序列 + 同花顺摘要 + 估值快照 · 每日缓存"),
        unsafe_allow_html=True)
    with st.spinner("拉取财务指标与财务摘要…"):
        snap = state.fetch_fundamentals(code6)
    if not snap or not snap.get("ok"):
        st.info("基本面数据获取失败（财务指标/摘要接口均未返回数据），"
                "请稍后重试；估值快照可在「总览」页查看。")
        return

    v, pf, g, s_, cf = (snap["valuation"], snap["profit"], snap["growth"],
                        snap["solvency"], snap["cashflow"])
    pct = lambda x: "--" if x is None else f"{x:+.2f}%"  # noqa: E731
    num = lambda x: "--" if x is None else f"{x:.2f}"  # noqa: E731

    g1, g2, g3, g4, g5 = st.columns(5)
    g1.markdown(ut.fund_group("估值", [
        ("市盈率(动)", num(v["pe"]), ""),
        ("市净率", num(v["pb"]), ""),
        ("总市值", _fmt_amt(v["mcap"]), "")]), unsafe_allow_html=True)
    g2.markdown(ut.fund_group("盈利能力", [
        ("ROE", num(pf["roe"]) + "%", "pos" if (pf["roe"] or 0) >= 15 else ""),
        ("毛利率", num(pf["gross_margin"]) + "%", ""),
        ("净利率", num(pf["net_margin"]) + "%", ""),
        ("每股收益", num(pf["eps"]) + "元", "")]), unsafe_allow_html=True)
    g3.markdown(ut.fund_group("成长性", [
        ("营收", _fmt_amt(g["revenue"]), ""),
        ("营收同比", pct(g["revenue_yoy"]),
         "pos" if (g["revenue_yoy"] or 0) > 0 else "neg"),
        ("净利", _fmt_amt(g["profit"]), ""),
        ("净利同比", pct(g["profit_yoy"]),
         "pos" if (g["profit_yoy"] or 0) > 0 else "neg"),
        ("扣非同比", pct(g["deduct_profit_yoy"]),
         "pos" if (g["deduct_profit_yoy"] or 0) > 0 else "neg")]),
        unsafe_allow_html=True)
    g4.markdown(ut.fund_group("偿债能力", [
        ("资产负债率", num(s_["debt_ratio"]) + "%", ""),
        ("流动比率", num(s_["current_ratio"]), ""),
        ("速动比率", num(s_["quick_ratio"]), "")]), unsafe_allow_html=True)
    g5.markdown(ut.fund_group("现金流", [
        ("每股经营现金流", num(cf["ocf_per_share"]) + "元", ""),
        ("报告期", snap.get("period", "--"), "")]), unsafe_allow_html=True)
    st.markdown(ut.fund_gap_note(snap.get("gaps")), unsafe_allow_html=True)

    st.plotly_chart(make_fundamental_figure(snap), width="stretch")
    st.caption("趋势图数据为单季口径（新浪财务指标），柱状图为按报告期累计值；"
               "银行/保险等行业的经营现金流为负属常态，请结合行业特征解读。"
               "财务数据来自公开接口，仅供研究参考。")


# ---------------------------------------------------------------- 历史回测

def get_or_build_backtest(data_key: str, df_all, strategy: str, fee_rate: float) -> dict:
    key = f"bt_{data_key}_{strategy}_{fee_rate:.4f}"
    if key not in st.session_state:
        st.session_state[key] = run_backtest(df_all, strategy=strategy,
                                             fee_rate=fee_rate)
    return st.session_state[key]


def render_backtest_panel(df_all, data_key: str, name: str):
    st.markdown(
        ut.sec_header("历史回测",
                      "信号评分策略（五组投票转多买/转空卖）· MA5/MA20 双均线 · "
                      "T+1 执行 · 涨跌停过滤 · 手续费单边"),
        unsafe_allow_html=True)
    c1, c2 = st.columns([1.3, 1])
    strategy = c1.radio(
        "策略", ["score", "ma_cross"], horizontal=True, key="bt_strategy",
        format_func=lambda v: {"score": "信号评分策略",
                               "ma_cross": "MA5/MA20 双均线"}[v])
    fee_w = c2.slider("手续费（万）", 0.0, 10.0, DEFAULT_FEE_RATE * 10000,
                      step=0.5, key="bt_fee")
    bt = get_or_build_backtest(data_key, df_all, strategy, fee_w / 10000.0)
    if not bt.get("ok"):
        st.info(bt.get("reason", "回测不可用"))
        return

    m = bt["metrics"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.markdown(ut.kpi("总收益", f"{m['total_return']:+.2f}%",
                       f"买入持有 {m['buy_hold_return']:+.2f}%",
                       "pos" if m["total_return"] > 0 else "neg"),
                unsafe_allow_html=True)
    k2.markdown(ut.kpi("年化收益", f"{m['annual_return']:+.2f}%", f"共{m['days']}天"),
                unsafe_allow_html=True)
    k3.markdown(ut.kpi("最大回撤", f"{m['max_drawdown']:.2f}%", "", "neg"),
                unsafe_allow_html=True)
    k4.markdown(ut.kpi("胜率", "-" if m["win_rate"] is None else f"{m['win_rate']:.1f}%",
                       "按已平仓交易"), unsafe_allow_html=True)
    k5.markdown(ut.kpi("交易次数", str(m["n_trades"]),
                       "含持仓中"), unsafe_allow_html=True)

    st.plotly_chart(make_backtest_figure(bt), width="stretch")
    trades = trades_df(bt)
    if not trades.empty:
        st.markdown(ut.sec_header("交易明细", "T+1：信号次日开盘价执行"),
                    unsafe_allow_html=True)
        st.dataframe(trades, hide_index=True, height=220)
    st.caption("回测为历史数据推演，不代表未来收益；未计印花税、滑点与冲击成本。"
               "信号评分策略的指标口径与「总览」页一致。")


# ---------------------------------------------------------------- AI 解读

def _reset_ai_context():
    state.reset_ai_context()


def _cached_news_digest(code6: str):
    """复用情报页已有结果，避免研究助手重复抓取。"""
    prefix = f"news_digest:{code6}:"
    candidates = [v for k, v in list(st.session_state.items())
                  if str(k).startswith(prefix) and getattr(v, "items", None)]
    if not candidates:
        return None
    return max(candidates, key=lambda d: str(getattr(d, "fetched_at", "")))


def render_ai_chat(ai: dict, df_all, result: dict, snapshot: dict,
                   data_key: str, code6: str = "", name: str = "",
                   symbol: str = "", period: str = 'daily', adjust: str = '', is_st: bool = False):
    st.divider()
    hcol, bcol = st.columns([3, 1])
    hcol.subheader("AI 研究助手")
    if bcol.button("🧹 清空对话", key="ai_clear"):
        _reset_ai_context()
        st.rerun()

    # 换标的/周期/复权 或 切换技能 → 重置上下文，防止串味
    if st.session_state.get("ai_data_key") != data_key:
        st.session_state["ai_data_key"] = data_key
        _reset_ai_context()
    if st.session_state.get("ai_skill_key") != ai["skill_name"]:
        st.session_state["ai_skill_key"] = ai["skill_name"]
        _reset_ai_context()

    msgs = st.session_state.setdefault("ai_messages", [])
    show_think = bool(ai.get("show_think", False))
    for m in msgs[-12:]:
        with st.chat_message(m["role"]):
            if m.get("reasoning") and show_think:
                with st.expander("模型推理（调试信息）"):
                    st.markdown(m["reasoning"])
            st.markdown(m["content"])
            if m.get("sources"):
                st.markdown(ut.sources_block(m["sources"]), unsafe_allow_html=True)

    st.caption("选择研究任务，助手会引用技术、财务与新闻证据，明确区分事实和推断。")
    active_projects = research_services.list_projects("active")
    project_map = {"不关联项目": None,
                   **{p["title"]: p["id"] for p in active_projects}}
    project_label = st.selectbox("结论归档项目", list(project_map),
                                 key=f"ai_project_{code6}",
                                 help="研究结论会连同证据、价格和有效期保存到个人研究库")
    validity_days = st.selectbox("结论有效期", (7, 14, 30, 60, 90), index=2,
                                 format_func=lambda x: f"{x} 天",
                                 key=f"ai_validity_{code6}")
    quick_disabled = not (ai["api_key"] and ai["model"])
    action_defs = {
        "earnings": ("业绩解读", """<task type="earnings">
解读最新财报。先核对报告期和口径，分析单季度营收/归母及扣非净利同比与环比、毛利/费用对利润的影响、并购并表与内生增长、现金流/应收/存货、实际业绩相对预告和一致预期。用“业绩拆解、盈利质量、下一跟踪指标、待确认问题”组织正文。缺资料时给明确缺口，不能用技术指标代替，不强行预测股价。
</task>"""),
        "panorama": ("全景研判", """<task type="panorama">
综合研究当前公司：经营价值、当前估值和短期交易条件分别如何？覆盖业务与竞争、经营质量、估值预期、行业产业链、事件、治理风险、市场及多周期技术，围绕最重要的2—4个驱动解释相互关系和反证。区分长期价值与短期波动，不按指标数量投票。条件与必要输入齐备时才给情景或估值计算，不硬填股价区间。引用实际证据，并指出哪些缺口会改变核心判断。
仅当[P1]含实际个人假设内容时审计其支持、冲突和待验证证据；空白占位不单列章节，不能迎合原假设。
</task>"""),
        "trade": ("交易计划", """<task type="trade_plan">
把现有证据转换为条件化交易计划。分别给出观察、试仓、加仓、减仓/退出条件；价位只能来自[T1]/[T3]/[K1]，并说明T+1、跳空和流动性风险。数据不足时不要硬给精确仓位或价格。
</task>"""),
        "risk": ("风险审计", """<task type="risk_audit">
站在反方审计当前结论。优先找信号冲突、样本偏差、估值/财务风险、回测局限、新闻可信度与执行风险；按严重度排序，并为每项给出验证指标和结论失效条件。
若存在[P1]，逐项检查核心假设和失效条件是否具体、可观测、可证伪。
</task>"""),
        "catalyst": ("新闻催化", """<task type="news_catalyst">
按重要性分析近期催化。逐条区分已披露事实、媒体报道和未证实传闻，引用[N#]；说明影响路径、可能已计价程度、下一验证节点和最大反证。没有[N#]证据时明确说明无法完成新闻催化判断。
</task>"""),
    }
    action = None
    action_cols = st.columns(len(action_defs))
    for col, (key, (label, _)) in zip(action_cols, action_defs.items()):
        if col.button(label, disabled=quick_disabled, key=f"ai_action_{key}",
                      width="stretch"):
            action = key
    if quick_disabled:
        st.caption("请先在左侧配置 API Key 与模型名；行情分析与新闻抓取仍可独立使用。")
    prompt = st.chat_input("继续追问，或要求核对某条证据…")

    user_q = None
    if action:
        user_q = action_defs[action][1]
    elif prompt:
        user_q = prompt.strip()
    if not user_q:
        return

    web_mode = ai.get("web_mode", "off")
    research_plan = research_services.build_research_plan(user_q, action or "custom")
    research_task_id = None
    try:
        research_task_id = research_services.start_research_task(
            code6, name, user_q, action or "custom",
            project_id=project_map[project_label])
    except Exception as task_error:  # noqa: BLE001
        st.warning(f"研究任务状态无法保存，将继续分析：{type(task_error).__name__}")

    # 新闻催化任务需要可追溯的原始条目；优先复用情报页缓存，没有才现场采集。
    digest = _cached_news_digest(code6) if code6 else None
    if action == "catalyst" and code6 and digest is None:
        try:
            with st.status("正在研究近 7 日新闻证据…", expanded=True) as news_workflow:
                digest = collect_news(code6, name, lookback_days=7,
                                      translate_en=False, research_config=ai,
                                      on_progress=lambda event: news_workflow.write(progress_text(event)))
                st.session_state[f"news_digest:{code6}:7:none"] = digest
                news_workflow.update(label="新闻研究流程结束（保留证据缺口）", state="complete", expanded=False)
        except Exception as e:  # noqa: BLE001
            st.warning(f"新闻证据采集失败，将按已有数据继续：{type(e).__name__}")
    news_text, local_sources = evidence_text(
        getattr(digest, "items", []) if digest else [], limit=12)
    if research_task_id:
        research_services.update_research_task(
            research_task_id, "search", evidence_count=len(local_sources),
            coverage=0.0)
    # 首次从无新闻切换到新闻催化时刷新系统上下文，但保留对话历史。
    personal_thesis = research_store.latest_thesis(code6) if code6 else None
    evidence_key = ("evidence-workflow-v4", code6, data_key, ai["skill_name"], web_mode,
                    getattr(digest, "fetched_at", "") if digest else "",
                    (personal_thesis or {}).get("id"))
    if st.session_state.get("ai_evidence_key") != evidence_key:
        for key in ("ai_system", "ai_context_message", "ai_meta", "ai_extra_params"):
            st.session_state.pop(key, None)
        st.session_state["ai_evidence_key"] = evidence_key
    st.session_state["ai_local_sources"] = local_sources

    if (not msgs or "ai_system" not in st.session_state
            or "ai_context_message" not in st.session_state):
        try:
            if research_task_id:
                research_services.update_research_task(research_task_id, "extract")
            with st.spinner("正在装配分析上下文（基本面 / 市场环境 / 回测 / 短线统计）…"):
                raw_md = skill_store.load_skill(ai["skill_name"]) or ""
                meta, body = parse_frontmatter(raw_md)
                bars_tail = None
                raw_tail = str(meta.get("bars_tail", "")).strip()
                if raw_tail.isdigit() and int(raw_tail) > 10:
                    bars_tail = int(raw_tail)
                # 思考模式控制（DeepSeek V4 等）：默认 auto=不发参数，走服务端默认
                extra_params = {}
                think_flag = str(meta.get("thinking", "")).strip().lower()
                if think_flag in ("on", "enabled", "true", "开"):
                    extra_params["thinking"] = {"type": "enabled"}
                elif think_flag in ("off", "disabled", "false", "关"):
                    extra_params["thinking"] = {"type": "disabled"}
                effort = str(meta.get("reasoning_effort", "")).strip().lower()
                if effort:
                    extra_params["reasoning_effort"] = effort
                # 装配期涉及多路行情/基本面/市场环境请求，akshare 部分请求
                # 不带超时，接口异常时会无限挂起导致「点击无反应」：
                # 兜底默认超时，失败自动降级为对应占位文本，不阻断对话
                with dtf.default_request_timeout(15.0):
                    fund_snap = state.fetch_fundamentals(code6)
                    company_context = state.company_context_cached(code6)
                    from astock.research.research_analytics import relative_performance_text
                    daily_df = df_all if period == 'daily' else None
                    if daily_df is None:
                        try:
                            daily_df = compute_all(state.load_history(symbol, 'daily', adjust, is_st), period='daily')
                        except Exception:
                            daily_df = None
                    benchmark, benchmark_id = state.benchmark_cached(code6)
                    market_ctx = relative_performance_text(daily_df, benchmark, benchmark_id, adjust)
                    if web_mode == 'off':
                        market_ctx = '\n'.join(x for x in (market_ctx, state.load_market_ctx(code6, name)) if x)
                    bt_default = get_or_build_backtest(
                        data_key, df_all, "score", DEFAULT_FEE_RATE)
                    daily_key = state.history_key(symbol, 'daily', adjust, is_st)
                    mp = get_or_build_multi(daily_key, daily_df,
                                            symbol=symbol,
                                            name=name) if code6 and daily_df is not None and len(daily_df) >= 2 else None
                    st_rep = (get_or_build_shortterm(daily_key, daily_df)
                              if code6 and daily_df is not None and len(daily_df) >= 2 else None)
                    placeholders = build_placeholders(
                        df_all, result, snapshot, bars_tail=bars_tail,
                        market_ctx=market_ctx,
                        fundamentals=fund.fundamentals_text(fund_snap),
                        backtest=backtest_text(bt_default, name=name),
                        multi_period=multi_period_text(mp),
                        shortterm=shortterm.shortterm_text(st_rep),
                        news=news_text,
                        personal_thesis=research_store.thesis_context(code6), bar_period=period)
                    placeholders.update(company_context)
                st.session_state["ai_system"] = render_system_prompt(body)
                st.session_state["ai_context_message"] = build_research_context(
                    placeholders, instrument=f"{name}（{code6}）")
                st.session_state["ai_meta"] = meta
                st.session_state["ai_extra_params"] = extra_params
        except Exception as e:
            if research_task_id:
                research_services.update_research_task(
                    research_task_id, "failed", error=f"上下文装配：{type(e).__name__}")
            st.error(f"分析上下文装配失败（行情接口可能超时）：{type(e).__name__}，"
                     "请稍后重试。")
            return

    try:
        meta = st.session_state.get("ai_meta") or {}
        temperature = float(meta.get("temperature", DEFAULT_TEMPERATURE))
        max_tokens = int(meta.get("max_tokens", DEFAULT_MAX_TOKENS))
        # 侧边栏显式选择优先于技能 frontmatter
        extra_params = dict(st.session_state.get("ai_extra_params") or {})
        if ai.get("think_mode") == "on":
            extra_params["thinking"] = {"type": "enabled"}
        elif ai.get("think_mode") == "off":
            extra_params["thinking"] = {"type": "disabled"}
        if ai.get("effort") and ai["effort"] != "auto":
            extra_params["reasoning_effort"] = ai["effort"]

        msgs.append({"role": "user", "content": user_q})
        with st.chat_message("user"):
            st.markdown(user_q)

        # 稳定规则置于 system；动态证据作为独立 user 上下文；当前问题始终在末尾。
        payload = ([{"role": "system", "content": st.session_state["ai_system"]},
                    {"role": "user", "content": st.session_state["ai_context_message"]}]
                   + msgs[-11:])
        box = st.chat_message("assistant")
        workflow = box.status("研究工作流：正在规划问题与准备证据…", expanded=False)
        workflow.write("研究子问题：\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(research_plan, 1)))
        note_md = box.empty()
        src_md = box.empty()
        holder = box.empty()
        buf_a: list = []
        sources: list = list(st.session_state.get("ai_local_sources") or [])
        search_report = None
        citation_validation = None
        analysis_plan = None
        analysis_review = None
        supplemental_evidence = {}
        notes: list = []
        err = None

        def _flush_notes():
            if notes:
                note_md.markdown("\n".join(f"> ℹ️ {n}" for n in notes))

        try:
            workflow.update(label="研究工作流：搜索证据与提取事实…", state="running")
            if research_task_id:
                research_services.update_research_task(research_task_id, "search")
            for kind, piece in run_agent(
                    ai["provider_cfg"], ai["model"], ai["api_key"],
                    payload, extra_params=extra_params, mode=web_mode,
                    budget=max_tokens, temperature=temperature,
                    on_progress=lambda event: workflow.write(progress_text(event))):
                if kind == "status":
                    notes.append(piece)
                    _flush_notes()
                    holder.markdown("▌")
                elif kind == "analysis_plan":
                    analysis_plan = piece
                    workflow.write("研究主问题：" + piece["core_question"])
                    workflow.write("研究维度：" + "；".join(d["label"] + "：" + d["question"] for d in piece["dimensions"]))
                    if research_task_id:
                        research_services.update_task_plan(research_task_id, piece)
                elif kind == "analysis_brief":
                    workflow.write(f"综合判断：已整理 {len(piece.get('drivers', []))} 个关键驱动")
                elif kind == 'supplemental_evidence':
                    supplemental_evidence.update(piece)
                    workflow.write(f'已取得 {len(piece)} 家候选同业的独立对照数据，可比性仍需结合业务判断')
                elif kind == "analysis_review":
                    analysis_review = piece
                    workflow.write(f"问题证据覆盖 {piece['coverage']:.0f}% · " +
                                   ("审查通过" if piece["complete"] else "仍有待解决的分析问题"))
                    labels = {d['id']: d['label'] for d in piece['plan']['dimensions']}
                    for item in piece.get('dimension_answers', []):
                        workflow.write(labels[item['dimension_id']] + '：' + item['status'] + ' · ' + item['answer'])
                elif kind == "research_report":
                    search_report = piece
                elif kind == "validation":
                    citation_validation = piece
                elif kind == "sources":
                    remote = list(piece or [])
                    seen_urls = {u for _, u in sources}
                    sources.extend((t, u) for t, u in remote if u not in seen_urls)
                elif kind == "answer":
                    buf_a.append(piece)
                    holder.markdown(piece)
        except Exception as e:
            err = str(e) if isinstance(e, AIError) else f"{type(e).__name__}: {e}"

        answer = "".join(buf_a)
        if research_task_id and answer:
            research_services.update_research_task(research_task_id, "conflicts")
        if answer:
            holder.markdown(answer)
        if sources:
            src_md.markdown(ut.sources_block(sources), unsafe_allow_html=True)

        run_id = None
        structured = None
        if answer and code6:
            workflow.update(label="研究工作流：交叉验证并归档可证伪结论…",
                            state="running")
            try:
                baseline = snapshot.get("price") if isinstance(snapshot, dict) else None
                if not baseline and df_all is not None and not df_all.empty:
                    baseline = float(df_all["close"].iloc[-1])
                stages = {name_: "pending" for name_ in (
                    "plan", "search", "extract", "cross_validate", "conflicts",
                    "impact_path", "scenarios", "invalidation", "monitoring")}
                stages["subquestions"] = [d['question'] for d in analysis_plan['dimensions']] if analysis_plan else research_plan
                stages["analysis_plan"] = analysis_plan
                stages['supplemental_evidence'] = supplemental_evidence
                stages["usage"] = {"model": ai.get("model") or "",
                                   "context_chars": sum(len(str(x.get("content", ""))) for x in payload),
                                   "answer_chars": len(answer), "source_count": len(sources)}
                from astock.ai.evidence import context_evidence
                evidence_registry, _ = context_evidence(payload)
                evidence_registry.update(supplemental_evidence)
                run_id = research_services.archive_research_run(
                    code6, name, user_q, answer, model=ai.get("model") or "",
                    task_type=action or "custom", sources=sources,
                    baseline_price=baseline, stage_state=stages,
                    project_id=project_map[project_label], valid_days=int(validity_days),
                    task_id=research_task_id, evidence_registry=evidence_registry,
                    search_report=search_report, citation_validation=citation_validation)
                rows = research_services.list_research_runs(code6, limit=1)
                structured = (rows[0] if rows and rows[0]["id"] == run_id else None)
            except Exception as archive_error:  # noqa: BLE001
                if research_task_id:
                    research_services.update_research_task(
                        research_task_id, "failed", error=f"归档：{type(archive_error).__name__}")
                notes.append(f"结论归档失败：{type(archive_error).__name__}")
                _flush_notes()
        if answer:
            if structured:
                state_label = "正式结论" if structured["status"] == "complete" else "不完整草稿"
                workflow.update(
                    label=f"研究工作流结束 · {state_label} #{run_id} · 引用可核验率 {structured['coverage']:.0f}%" +
                          (f" · 问题证据覆盖 {analysis_review['coverage']:.0f}%" if analysis_review else ""),
                    state="complete", expanded=structured["status"] != "complete")
                if structured["status"] != "complete":
                    try:
                        parsed = json.loads(structured["structured_json"])
                        missing = [*parsed.get("missing_fields", []), *parsed.get("verification", {}).get("gaps", [])]
                    except Exception:
                        missing = []
                    workflow.write("仍缺少：" + ("、".join(missing) if missing else "结构化字段或有效引用"))
                else:
                    try:
                        parsed = json.loads(structured["structured_json"])
                    except Exception:
                        parsed = {}
                    workflow.write(
                        f"证据编号 {len(parsed.get('citations') or [])} 个 · "
                        f"冲突/反证 {'已覆盖' if parsed.get('opposing_facts') else '未覆盖'} · "
                        f"待确认问题 {'已列出' if parsed.get('unknowns') else '未列出'}")
            else:
                workflow.update(label="研究回答已生成，但未能归档", state="error")
        else:
            if research_task_id:
                research_services.update_research_task(
                    research_task_id, "failed", error=err or "模型未返回正文")
            workflow.update(label="研究任务未生成有效结论", state="error")

        entry = {"role": "assistant", "content": answer}
        if sources:
            entry["sources"] = sources
        if answer:
            msgs.append(entry)
        if err:
            note = f"> ⚠️ {'流式输出中断' if answer else 'AI 调用失败'}：{err}"
            if not (answer or reasoning):
                msgs.append({"role": "assistant", "content": note})
                with st.chat_message("assistant"):
                    st.markdown(note)
            else:
                st.warning(note)
    except AIError as e:
        if research_task_id:
            research_services.update_research_task(
                research_task_id, "failed", error=f"AIError:{str(e)[:200]}")
        st.error(f"AI 调用失败：{e}")


# ---------------------------------------------------------------- 入口

def render_tech_panel(ai: dict, bundle: dict):
    df_all = bundle["df_all"]
    df = bundle["df"]
    result = bundle["result"]
    data_key = state.history_key(bundle["symbol"], bundle["period"],
                                 bundle["adjust"], bundle["is_st"])
    code6 = bundle["symbol"][2:]

    views = {
        "overview": "综合概览", "multi": "多周期", "fundamental": "基本面",
        "backtest": "策略回测", "assistant": "AI 研究助手",
    }
    st.session_state.setdefault("tech_view", "overview")
    view = st.segmented_control(
        "研究模块", list(views), key="tech_view",
        format_func=lambda x: views[x], label_visibility="collapsed",
        width="stretch") or "overview"
    if view == "overview":
        render_overview_panel(df_all, df, result, bundle["name"],
                              bundle["symbol"], bundle["symbol_disp"])
    elif view == "multi":
        render_multi_period_panel(df_all, bundle["symbol"], bundle["name"],
                                  data_key)
    elif view == "fundamental":
        render_fundamentals_panel(code6, bundle["name"])
    elif view == "backtest":
        render_backtest_panel(df_all, data_key, bundle["name"])
    elif view == "assistant":
        render_skill_editor()
        render_ai_chat(ai, df_all, result, bundle["snap"], data_key,
                       code6=code6, name=bundle["name"],
                       symbol=bundle["symbol"], period=bundle['period'],
                       adjust=bundle['adjust'], is_st=bundle['is_st'])
