import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import data_fetcher as dtf
from indicators import compute_all
from report import BULL, BEAR, build_report

import skill_store
import ui_theme as ut
from ai_client import (AIError, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE,
                       PROVIDERS, chat_complete, clear_local_config,
                       load_local_config, save_local_config)
from ai_context import build_placeholders, parse_frontmatter, render_prompt

st.set_page_config(page_title="A股技术分析工具", page_icon="📈", layout="wide")

UP = ut.UP
DOWN = ut.DOWN

_MA_COLORS = {
    "ma5": "#f39c12",
    "ma10": "#3498db",
    "ma20": "#9b59b6",
    "ma60": "#16a085",
    "ma120": "#e67e22",
    "ma250": "#7f8c8d",
}


@st.cache_data(ttl=300, show_spinner=False)
def load_history(symbol: str, period: str, adjust: str, is_st: bool) -> pd.DataFrame:
    return dtf.get_history(symbol, period, adjust, is_st=is_st)


def make_figure(df: pd.DataFrame) -> go.Figure:
    x = df["date"].astype(str)
    fig = make_subplots(
        rows=5, cols=1, shared_xaxes=True,
        row_heights=[0.42, 0.12, 0.16, 0.15, 0.15],
        vertical_spacing=0.03,
        subplot_titles=("K线 / 均线 / 布林带", "成交量", "MACD", "KDJ", "RSI"),
    )
    fig.add_trace(go.Candlestick(
        x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color=UP, decreasing_line_color=DOWN,
        increasing_fillcolor=UP, decreasing_fillcolor=DOWN,
        name="K线", showlegend=False,
    ), row=1, col=1)
    for col, c in _MA_COLORS.items():
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(x=x, y=df[col], name=col.upper(),
                                 line=dict(width=1.2, color=c)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["boll_up"], name="BOLL上/下轨",
                             line=dict(width=1, dash="dot", color="#95a5a6")), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["boll_low"], name="BOLL带",
                             line=dict(width=1, dash="dot", color="#95a5a6"),
                             fill="tonexty", fillcolor="rgba(149,165,166,0.08)"), row=1, col=1)

    vol_colors = [UP if c >= o else DOWN for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=x, y=df["volume"], marker_color=vol_colors,
                         name="成交量", showlegend=False), row=2, col=1)
    vol_ma = df["vol_ma5"] if "vol_ma5" in df.columns else df["volume"].rolling(5).mean()
    fig.add_trace(go.Scatter(x=x, y=vol_ma, name="VOL-MA5",
                             line=dict(width=1, color="#f39c12"), showlegend=False), row=2, col=1)

    macd_colors = [UP if v >= 0 else DOWN for v in df["macd"]]
    fig.add_trace(go.Bar(x=x, y=df["macd"], marker_color=macd_colors,
                         name="MACD柱", showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["dif"], name="DIF",
                             line=dict(width=1.2, color="#f39c12")), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["dea"], name="DEA",
                             line=dict(width=1.2, color="#3498db")), row=3, col=1)
    fig.add_hline(y=0, line=dict(width=0.8, color="#bdc3c7"), row=3, col=1)

    fig.add_trace(go.Scatter(x=x, y=df["kdj_k"], name="K",
                             line=dict(width=1.2, color="#f39c12")), row=4, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["kdj_d"], name="D",
                             line=dict(width=1.2, color="#3498db")), row=4, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["kdj_j"], name="J",
                             line=dict(width=1, color="#9b59b6")), row=4, col=1)
    for level in (80, 20):
        fig.add_hline(y=level, line=dict(width=0.8, color="#bdc3c7", dash="dash"), row=4, col=1)

    fig.add_trace(go.Scatter(x=x, y=df["rsi6"], name="RSI6",
                             line=dict(width=1.2, color="#f39c12")), row=5, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["rsi12"], name="RSI12",
                             line=dict(width=1, color="#3498db")), row=5, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["rsi24"], name="RSI24",
                             line=dict(width=1, color="#16a085")), row=5, col=1)
    for level in (80, 50, 20):
        fig.add_hline(y=level, line=dict(width=0.8, color="#bdc3c7", dash="dash"), row=5, col=1)

    fig.update_layout(
        height=1080,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=ut.FONT_SANS.replace("'", '"'), size=11.5, color=ut.TEXT_DIM),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        margin=dict(l=8, r=14, t=34, b=6),
    )
    fig.update_xaxes(type="category", rangeslider_visible=False,
                     gridcolor="#20262E", linecolor="#2C3640",
                     tickfont=dict(size=10.5))
    fig.update_yaxes(gridcolor="#20262E", zerolinecolor="#2C3640",
                     tickfont=dict(size=10.5), fixedrange=False)
    # 各子图纵轴单位标注
    fig.update_yaxes(title_text="价格 (元)", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    return fig


def render_sidebar():
    with st.sidebar:
        st.header("⚙️ 分析设置")
        samples = [("600519", "茅台"), ("000001", "平安银行"),
                   ("300750", "宁德时代"), ("601318", "中国平安")]
        q1, q2 = st.columns(2)
        for i, (code_, label) in enumerate(samples):
            box = q1 if i % 2 == 0 else q2
            if box.button(f"{label}", width="stretch", key=f"q{i}"):
                st.session_state["code_field"] = code_
                st.session_state["_pending_code"] = code_
                st.session_state["_pending_run"] = True
        code = st.text_input("股票代码（6位）", key="code_field")
        period = st.radio("分析周期", ["daily", "weekly"],
                          format_func=lambda p: "日线" if p == "daily" else "周线",
                          horizontal=True)
        adjust = st.selectbox("复权方式", ["qfq", "", "hfq"],
                              format_func=lambda a: {"qfq": "前复权", "hfq": "后复权", "": "不复权"}[a])
        n_bars = st.slider("图表显示根数", 60, 300, 150)
        run = st.button("🔍 开始分析", type="primary", width="stretch")
    return code, period, adjust, n_bars, run


def ensure_data(symbol: str, period: str, adjust: str, is_st: bool) -> bool:
    cache_key = f"{symbol}|{period}|{adjust}|{int(is_st)}"
    if st.session_state.get("data_key") == cache_key:
        return True
    with st.spinner("拉取行情数据并计算指标…"):
        try:
            raw = load_history(symbol, period, adjust, is_st)
        except RuntimeError as e:
            st.error(f"数据获取失败：{e}")
            return False
    st.session_state["df_all"] = compute_all(raw, period=period)
    st.session_state["data_key"] = cache_key
    return True


def render_ai_settings() -> dict:
    """侧边栏 AI 设置：厂商/BaseURL/模型/Key/本机记忆 + 技能选择。"""
    with st.sidebar:
        st.divider()
        st.header("🤖 AI 解读")
        saved = load_local_config()
        env_key = os.environ.get("ASTOCK_AI_KEY", "")
        prov_ids = list(PROVIDERS.keys())
        default_pid = saved.get("provider") if saved.get("provider") in PROVIDERS else "deepseek"
        pid = st.selectbox("厂商", prov_ids,
                           index=prov_ids.index(default_pid),
                           format_func=lambda x: PROVIDERS[x]["label"],
                           key="ai_provider")
        first = "_ai_prev_provider" not in st.session_state
        prev = st.session_state.get("_ai_prev_provider")
        if first or prev != pid:
            st.session_state["_ai_prev_provider"] = pid
            info = PROVIDERS[pid]
            if first and saved.get("base_url"):
                st.session_state.setdefault("ai_base_url", saved["base_url"])
                st.session_state.setdefault("ai_model", saved.get("model", ""))
            else:
                st.session_state["ai_base_url"] = info["base_url"]
                st.session_state["ai_model"] = info["models"][0] if info["models"] else ""
            if first and saved.get("api_key") and not env_key:
                pass  # 下方 value= 会带入 saved 的 Key

        base_url = st.text_input("Base URL", key="ai_base_url")
        hint_models = "，".join(PROVIDERS[pid]["models"]) or "如 http://localhost:11434/v1 + 模型名"
        model = st.text_input("模型", key="ai_model",
                              help=f"常见模型：{hint_models}")
        api_key = st.text_input("API Key", value=env_key or saved.get("api_key", ""),
                                type="password",
                                help="优先级：环境变量 ASTOCK_AI_KEY > 本机保存 > 手动输入")
        remember = st.checkbox("记住到本机（.streamlit/secrets.toml，明文）",
                               value=bool(saved.get("api_key")) and not env_key,
                               disabled=bool(env_key))
        c1, c2 = st.columns(2)
        if c1.button("💾 保存本机", disabled=not (remember and api_key)):
            save_local_config({"provider": pid, "base_url": base_url,
                               "model": model, "api_key": api_key})
            st.success("已保存，下次启动免输入")
        if c2.button("🗑 清除本机", disabled=not saved):
            clear_local_config()
            st.rerun()

        skills = skill_store.list_skills()
        skill_name = st.selectbox("分析技能", skills or ["default"],
                                  index=(skills.index("default") if "default" in skills else 0),
                                  key="ai_skill_sel")
        st.caption("提示词可在下方「技能编辑器」中修改并保存。")

        st.divider()
        think_mode = st.selectbox(
            "思考模式开关", ["auto", "on", "off"],
            format_func=lambda v: {"auto": "跟随技能/厂商默认",
                                   "on": "强制开启", "off": "关闭"}[v],
            key="ai_think_mode",
            help="DeepSeek V4 服务端默认开启思考且强度 high；"
                 "显式选择将覆盖技能 frontmatter 配置")
        effort = st.selectbox(
            "思考强度", ["auto", "low", "medium", "high", "xhigh", "max"],
            format_func=lambda v: "跟随技能/默认" if v == "auto" else v,
            key="ai_effort",
            help="DeepSeek V4 映射：medium→high；仅支持该参数的厂商生效")
        show_think = st.toggle("💭 显示思考链", value=True, key="ai_show_think",
                               help="关闭后不渲染思维链，不影响模型思考与对话质量")
    return {"provider_cfg": PROVIDERS[pid], "model": model.strip(),
            "api_key": api_key.strip(), "skill_name": skill_name,
            "think_mode": think_mode, "effort": effort,
            "show_think": show_think}


def render_skill_editor():
    with st.expander("✏️ 技能编辑器（Markdown 提示词，保存后立即生效）"):
        skills = skill_store.list_skills()
        mode = st.radio("操作", ("编辑现有", "新建技能"), horizontal=True, key="ed_mode")
        if mode == "编辑现有":
            if not skills:
                st.info("skills 目录暂无技能文件")
                return
            name = st.selectbox("选择技能", skills, key="ed_sel")
            content = skill_store.load_skill(name) or ""
            prev_sel = st.session_state.get("_ed_prev")
            if prev_sel != name:
                st.session_state["_ed_prev"] = name
                st.session_state["ed_body"] = content
            edited = st.text_area("内容（支持占位符 {report} {plan} {bars} {snapshot}）",
                                  value=content, height=440, key="ed_body")
            new_name = st.text_input("另存为（留空则覆盖原文件）", key="ed_as")
            col1, col2, col3 = st.columns([1, 1.4, 2])
            if col1.button("💾 保存", key="ed_save"):
                target = new_name.strip() or name
                skill_store.save_skill(target, edited)
                st.success(f"已保存：{skill_store._safe_name(target)}")
                st.rerun()
            if col2.button("🗑 删除该技能", key="ed_del"):
                skill_store.delete_skill(name)
                st.rerun()
        else:
            title = st.text_input("新技能名称", placeholder="如：打板复盘", key="ed_new_name")
            if st.button("从模板创建", key="ed_create"):
                clean = title.strip()
                if not clean:
                    st.warning("请先输入名称")
                else:
                    skill_store.save_skill(clean, skill_store.new_skill_content(clean))
                    st.success(f"已创建 {skill_store._safe_name(clean)}，请切回「编辑现有」完善内容")
                    st.rerun()


def _reset_ai_context():
    for k in ("ai_messages", "ai_system", "ai_meta", "ai_extra_params"):
        st.session_state.pop(k, None)


def _load_market_ctx(code6: str, name: str) -> str:
    """拉取市场环境快照（情绪/板块/消息/龙虎榜/北向），失败降级为提示文本。"""
    if not code6:
        return ""
    try:
        with st.spinner("📡 拉取市场环境数据（涨跌家数/涨停连板/板块/龙虎榜/新闻）…"):
            from market_context import build_market_context
            return build_market_context(code6, name)
    except Exception as e:  # noqa: BLE001
        return f"（市场环境数据采集失败：{type(e).__name__}: {e}）"


def render_ai_chat(ai: dict, df_all, result: dict, snapshot: dict,
                   data_key: str, code6: str = "", name: str = ""):
    st.divider()
    hcol, bcol = st.columns([3, 1])
    hcol.subheader("🤖 AI 解读（多轮对话）")
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
    show_think = bool(ai.get("show_think", True))
    for m in msgs[-12:]:
        with st.chat_message(m["role"]):
            if m.get("reasoning") and show_think:
                with st.expander("💭 查看思考链"):
                    st.markdown(m["reasoning"])
            st.markdown(m["content"])

    quick_disabled = not (ai["api_key"] and ai["model"])
    quick = st.button("⚡ 一键综合解读", disabled=quick_disabled, key="ai_quick",
                      help="首次发送将自动注入信号报告、价位参考与近90根K线数据")
    prompt = st.chat_input("追问…（首次发送自动注入完整技术数据）")

    user_q = None
    if quick and not msgs:
        user_q = "请基于以上数据给出综合研判、关键价位核对与操作建议（含风险提示）。"
    elif prompt:
        user_q = prompt.strip()
    if not user_q:
        return

    try:
        if not msgs or "ai_system" not in st.session_state:
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
            placeholders = build_placeholders(df_all, result, snapshot,
                                              bars_tail=bars_tail,
                                              market_ctx=_load_market_ctx(code6, name))
            st.session_state["ai_system"] = render_prompt(body, placeholders)
            st.session_state["ai_meta"] = meta
            st.session_state["ai_extra_params"] = extra_params
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

        payload = ([{"role": "system", "content": st.session_state["ai_system"]}]
                   + msgs[-12:])
        box = st.chat_message("assistant")
        status = box.status("🤔 模型思考中…", expanded=False) if show_think else None
        think_md = status.empty() if status else None
        note_md = box.empty()
        holder = box.empty()
        buf_r: list = []
        buf_a: list = []
        notes: list = []
        err = None
        try:
            gen = chat_complete(ai["provider_cfg"], ai["model"], ai["api_key"],
                                payload, temperature=temperature,
                                max_tokens=max_tokens,
                                extra_params=extra_params)
            for kind, piece in gen:
                if kind == "reasoning":
                    buf_r.append(piece)
                    if think_md is not None:
                        think_md.markdown("".join(buf_r)[-6000:])
                elif kind == "content":
                    buf_a.append(piece)
                    holder.markdown("".join(buf_a) + "▌")
                elif kind == "restart":
                    # 零正文即被截断：清空已渲染内容，按更大预算重新开始
                    buf_r.clear()
                    buf_a.clear()
                    notes.append(piece)
                    note_md.markdown("\n".join(f"> ℹ️ {n}" for n in notes))
                    if status is not None:
                        status.update(label=piece, state="running", expanded=True)
                        think_md.markdown("")
                    holder.markdown("")
                elif kind == "notice":
                    notes.append(piece)
                    note_md.markdown("\n".join(f"> ℹ️ {n}" for n in notes))
        except Exception as e:
            err = str(e) if isinstance(e, AIError) else f"{type(e).__name__}: {e}"

        answer = "".join(buf_a)
        reasoning = "".join(buf_r)
        if status is not None:
            if reasoning:
                think_md.markdown(reasoning[-6000:])
            status.update(
                label="💭 思考链" if reasoning else "思考链（本次无）",
                state="error" if (err and not answer) else "complete",
                expanded=bool(reasoning) and not answer,
            )
        if answer:
            holder.markdown(answer)

        if answer or reasoning:
            msgs.append({"role": "assistant", "content": answer,
                         "reasoning": reasoning})
        if err:
            note = f"> ⚠️ {'流式输出中断' if answer else 'AI 调用失败'}：{err}"
            if not (answer or reasoning):
                msgs.append({"role": "assistant", "content": note})
                with st.chat_message("assistant"):
                    st.markdown(note)
            else:
                st.warning(note)
    except AIError as e:
        st.error(f"AI 调用失败：{e}")


@st.cache_data(ttl=60, show_spinner=False)
def fetch_spot_extra(code6: str) -> dict:
    """东财盘口快照中的估值字段（市盈率/市净率/市值等）。失败返回空 dict。"""
    try:
        import akshare as ak
        df = ak.stock_bid_ask_em(symbol=code6)
        return dict(zip(df["item"].astype(str), df["value"]))
    except Exception:
        return {}


def _pick_val(d: dict, *keywords):
    for k, v in d.items():
        if any(w in str(k) for w in keywords):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _fmt_amt(v):
    if v is None:
        return "--"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.0f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f} 万"
    return f"{v:.0f}"


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


def main():
    st.markdown(ut.apply_global_css(), unsafe_allow_html=True)

    code, period, adjust, n_bars, run = render_sidebar()
    ai = render_ai_settings()

    pending = st.session_state.pop("_pending_code", None)
    if pending:
        st.rerun()

    if not (code and code.strip().isdigit()):
        st.info("请在左侧输入6位股票代码（如 600519），或点击快捷按钮开始分析。")
        return

    try:
        symbol = dtf.normalize_symbol(code)
    except ValueError as e:
        st.error(str(e))
        return

    triggered = run or st.session_state.pop("_pending_run", False)
    query = (symbol, period, adjust)
    if triggered:
        st.session_state["active_query"] = query
    if st.session_state.get("active_query") != query:
        st.info("请点击「开始分析」加载或刷新该标的（修改周期/复权后也需重新分析）。")
        return

    snap = dtf.fetch_realtime(symbol)
    name = snap.get("name") or symbol[2:]
    is_st = dtf.is_st_name(name)

    if not ensure_data(symbol, period, adjust, is_st):
        return

    df_all: pd.DataFrame = st.session_state["df_all"]
    if len(df_all) < 2:
        st.error("有效K线不足，无法分析。")
        return
    df = df_all.tail(n_bars).reset_index(drop=True)

    suffix = {"sh": "SH", "sz": "SZ"}.get(symbol[:2], "BJ")
    symbol_disp = f"{symbol[2:]}.{suffix}"
    period_label = "日线" if period == "daily" else "周线"
    adjust_label = {"qfq": "前复权", "hfq": "后复权", "": "不复权"}[adjust]

    # ---------- 一、顶部行情概览栏（一级信息） ----------
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
               "仅供学习研究，不构成任何投资建议")

    result = build_report(df_all, name=name, symbol=symbol_disp, period=period)
    s = result["score"]

    # ---------- 二、AI 综合结论横幅（一级信息） ----------
    ops = ("操作提示：A股 T+1，信号按已收盘K线计，最早下一交易日开盘关注；"
           "空头仅指减仓/回避，不假设可做空。")
    st.markdown(ut.verdict_banner(s["verdict"], s["tone"], s["bull"], s["bear"], ops),
                unsafe_allow_html=True)

    # ---------- 三、关键指标卡片区（二级信息） ----------
    st.markdown(ut.sec_header("关键指标", "技术面派生 + 东财估值快照；'--' 表示数据缺失"),
                unsafe_allow_html=True)
    extras = fetch_spot_extra(symbol[2:])
    close = last_close
    cards = []

    turnover = _pick_val(extras, "换手率")
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

    pe = _pick_val(extras, "市盈率")
    pb = _pick_val(extras, "市净率")
    mcap = _pick_val(extras, "总市值")
    cards.append(("市盈率(动)", f"{pe:.1f}" if pe is not None else "--", ""))
    cards.append(("市净率", f"{pb:.2f}" if pb is not None else "--", ""))
    cards.append(("总市值", _fmt_amt(mcap) if mcap is not None else "--", ""))

    kc = st.columns(len(cards))
    for col, (label, value, sub, *rest) in zip(kc, cards):
        cls = rest[0] if rest else ""
        col.markdown(ut.kpi(label, value, sub, cls), unsafe_allow_html=True)

    # ---------- 四、技术图表区 ----------
    st.markdown(ut.sec_header("技术图表", "K线/均线/布林 · 成交量 · MACD · KDJ · RSI"),
                unsafe_allow_html=True)
    st.plotly_chart(make_figure(df), use_container_width=True)

    # ---------- 五、AI 信号与风险提示区 ----------
    plan = result.get("plan")
    left, right = st.columns([1.35, 1])
    with left:
        st.markdown(ut.sec_header("AI 信号明细", "五大指标组 · 每组一票"),
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

    data_key = f"{symbol}|{period}|{adjust}|{int(is_st)}"
    render_skill_editor()
    render_ai_chat(ai, df_all, result, snap, data_key,
                   code6=symbol[2:], name=name)

    st.divider()
    st.caption("⚠️ 免责声明：本工具输出仅为基于历史数据的技术指标计算结果与模型推演，"
               "不构成任何投资建议。股市有风险，入市需谨慎。")


if __name__ == "__main__":
    main()
