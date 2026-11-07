"""统一数据层：行情/指标/报告/基本面/多周期装配与缓存。

职责：
- 行情历史与指标：st.cache_data + session_state 双缓存，一次拉取多场景复用；
- 估值快照：session 级 TTL 缓存（成功 600s / 失败仅 60s，失败结果不长期滞留）；
- 财务指标与摘要（86400s）、市场环境（600s）分级缓存；
- 全市场代码列表：按天缓存 + 本地磁盘兜底（重启服务不重爬）；
- 会话键与旧版 app.py 保持一致，拆分视图后行为不变。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from astock.market import data_fetcher as dtf
from astock.analysis.indicators import compute_all

AI_CONTEXT_KEYS = ("ai_messages", "ai_system", "ai_context_message",
                   "ai_meta", "ai_extra_params", "ai_evidence_key",
                   "ai_local_sources")

_SPOT_OK_TTL = 600.0     # 估值快照成功缓存时长（秒）
_SPOT_ERR_TTL = 60.0     # 失败结果仅短暂缓存，接口恢复后自动重试
_RT_OK_TTL = 60.0        # 实时行情快照成功缓存时长（秒）
_RT_ERR_TTL = 15.0       # 失败结果短缓存，避免每次 rerun 都重试打爆接口
_CODES_CACHE_TTL = 86400.0   # 代码列表本地缓存时长（秒）
_CODES_CACHE_PATH = Path(".cache_stock_codes.json")


# ---------------------------------------------------------------- 行情与指标

def history_key(symbol: str, period: str, adjust: str, is_st: bool) -> str:
    return f"{symbol}|{period}|{adjust}|{int(is_st)}"


def fetch_realtime_cached(symbol: str) -> dict:
    """实时行情快照（session 级 TTL 缓存）。

    每次 Streamlit rerun 都会调用实时接口，无缓存时网络抖动会拖死整页；
    成功缓存 60s，失败仅 15s（接口恢复后自动重试，不打爆接口）。
    """
    key = f"rt_{symbol}"
    now = time.time()
    entry = st.session_state.get(key)
    if entry:
        data, ts = entry
        ttl = _RT_OK_TTL if data else _RT_ERR_TTL
        if now - ts < ttl:
            return data
    data = dtf.fetch_realtime(symbol)
    st.session_state[key] = (data, now)
    return data


@st.cache_data(ttl=300, show_spinner=False)
def _load_raw_daily(symbol: str) -> pd.DataFrame:
    """不复权原始日线（涨跌停判定基准）。独立缓存：与复权参数无关，
    复权模式下 get_history 不再二次全量拉取同一数据源。"""
    try:
        return dtf.fetch_daily(symbol, "", min_rows=dtf.MIN_DAILY_BARS)
    except RuntimeError:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_history(symbol: str, period: str, adjust: str, is_st: bool) -> pd.DataFrame:
    raw = _load_raw_daily(symbol) if adjust else None
    return dtf.get_history(symbol, period, adjust, is_st=is_st,
                           raw_df=raw if raw is not None and not raw.empty else None)


def ensure_df_all(symbol: str, period: str, adjust: str, is_st: bool):
    """返回 (df_all, err)。df_all 已计算指标；失败返回 (None, 错误文案)。"""
    key = history_key(symbol, period, adjust, is_st)
    if st.session_state.get("data_key") == key:
        df = st.session_state.get("df_all")
        if df is not None and len(df):
            return df, None
    with st.spinner("拉取行情数据并计算指标…"):
        try:
            raw = load_history(symbol, period, adjust, is_st)
        except RuntimeError as e:
            return None, f"数据获取失败：{e}"
    if raw is None or len(raw) < 2:
        return None, "K线不足，无法分析。"
    try:
        df_all = compute_all(raw, period=period)
    except Exception as e:  # noqa: BLE001 —— 数据源字段异常不击穿到页面
        return None, f"指标计算失败：{type(e).__name__}"
    if df_all is None or len(df_all) < 2:
        return None, "K线不足，无法分析。"
    st.session_state["df_all"] = df_all
    st.session_state["data_key"] = key
    return df_all, None


# ---------------------------------------------------------------- 估值 / 基本面 / 市场环境

def fetch_spot_extra(code6: str) -> dict:
    """估值快照（session 级 TTL 缓存）。空结果只缓存 60s，避免接口故障时
    把缺失数据滞留一小时。"""
    key = f"spot_extra_{code6}"
    now = time.time()
    entry = st.session_state.get(key)
    if entry:
        data, ts = entry
        ttl = _SPOT_OK_TTL if data else _SPOT_ERR_TTL
        if now - ts < ttl:
            return data
    from astock.market.fundamentals import fetch_spot_extra as _fse
    data = _fse(code6)
    st.session_state[key] = (data, now)
    return data


@st.cache_data(ttl=86400, show_spinner=False)
def _fundamentals_core(code6: str):
    """财务指标序列 + 同花顺摘要（每日缓存一次；估值部分单独缓存）。"""
    from astock.market.fundamentals import fetch_financial_abstract, fetch_financial_indicator
    return (fetch_financial_indicator(code6), fetch_financial_abstract(code6))


def fetch_fundamentals(code6: str) -> dict:
    """基本面快照（含趋势序列）。财务数据日频缓存，估值快照独立 TTL 刷新。"""
    from astock.market.fundamentals import build_fundamentals
    indicator_df, abstract_df = _fundamentals_core(code6)
    extras = fetch_spot_extra(code6)
    return build_fundamentals(indicator_df, abstract_df, extras=extras)


@st.cache_data(ttl=600, show_spinner=False)
def market_context_cached(code6: str, name: str) -> str:
    from astock.market.market_context import build_market_context
    return build_market_context(code6, name)


@st.cache_data(ttl=86400, show_spinner=False)
def _stock_codes() -> list:
    """A股代码全列表（按天缓存；磁盘兜底，重启服务后无需重爬）。

    交易所代码列表接口较慢（约20秒），先读本地 24 小时缓存文件，
    失效才重新抓取；抓取失败不落盘，避免坏数据滞留。
    """
    try:
        cached = json.loads(_CODES_CACHE_PATH.read_text(encoding="utf-8"))
        if (cached.get("ts") and cached.get("codes")
                and time.time() - float(cached["ts"]) < _CODES_CACHE_TTL):
            return [str(c) for c in cached["codes"]]
    except Exception:  # noqa: BLE001 —— 缓存文件缺失/损坏走网络
        pass
    from astock.market.screener import fetch_codes
    codes = fetch_codes()
    if codes:
        try:
            _CODES_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CODES_CACHE_PATH.write_text(
                json.dumps({"ts": time.time(), "codes": codes}),
                encoding="utf-8")
        except Exception:  # noqa: BLE001 —— 落盘失败不影响本次使用
            pass
    return codes


def fetch_spot_market_live(include_gex: bool = True,
                           max_age: float = 15.0) -> tuple:
    """动态扫盘用全市场快照（session 级短 TTL 缓存），返回 (df, source, fresh)。

    max_age 秒内复用缓存（普通 rerun 不卡页面）；超时重拉。
    fresh=True 表示本次发生了实际网络拉取（AI 联动据此判断是否重新精选）。
    include_gex=False 时在拉取前就过滤掉创科代码，减少批量请求量。
    """
    key = f"spot_market_live_{int(bool(include_gex))}"
    now = time.time()
    entry = st.session_state.get(key)
    if entry:
        data, ts = entry
        if now - ts < max_age:
            return data[0], data[1], False
    from astock.market.screener import fetch_spot_market as _fsm
    from astock.market.screener import filter_codes_for_scope
    codes = filter_codes_for_scope(_stock_codes(), include_gex=include_gex)
    data = _fsm(codes=codes)
    st.session_state[key] = (data, now)
    return data[0], data[1], True


def force_spot_market_refresh(include_gex: bool = True):
    """手动扫描：丢弃会话快照，下次调用立即重拉。"""
    st.session_state.pop(f"spot_market_live_{int(bool(include_gex))}", None)


def load_market_ctx(code6: str, name: str) -> str:
    """市场环境快照；失败降级为提示文本，不阻断分析。"""
    if not code6:
        return ""
    try:
        return market_context_cached(code6, name)
    except Exception as e:  # noqa: BLE001
        return f"（市场环境数据采集失败：{type(e).__name__}: {e}）"


# ---------------------------------------------------------------- 会话状态辅助

def reset_ai_context():
    for k in AI_CONTEXT_KEYS:
        st.session_state.pop(k, None)


def active_query() -> tuple:
    return st.session_state.get("active_query")


def set_active_query(query: tuple):
    st.session_state["active_query"] = query


def pending_rerun_marker() -> bool:
    """快捷按钮触发的一键分析标记：设置后由 main 消费并 rerun。"""
    return bool(st.session_state.pop("_pending_run", False))
