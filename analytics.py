"""面向 UI 的派生分析摘要。

这里不制造新的买卖信号，只把价格、波动和已有的五组投票压缩成可解释的
研究快照。所有百分比均来自已收盘 K 线，便于测试和复用。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def _number(value) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _period_return(close: pd.Series, bars: int) -> Optional[float]:
    """返回 bars 个周期的区间收益率（百分数）。"""
    values = pd.to_numeric(close, errors="coerce").dropna()
    if len(values) <= bars or values.iloc[-bars - 1] <= 0:
        return None
    return (float(values.iloc[-1]) / float(values.iloc[-bars - 1]) - 1.0) * 100


def _max_drawdown(close: pd.Series, bars: int = 60) -> Optional[float]:
    values = pd.to_numeric(close, errors="coerce").dropna().tail(bars)
    if len(values) < 2:
        return None
    peak = values.cummax()
    return float((values / peak - 1.0).min() * 100)


def _trend(df: pd.DataFrame) -> tuple[str, str, str]:
    """用均线结构描述趋势，不替代 signals 模块的正式投票。"""
    last = df.iloc[-1]
    close = _number(last.get("close"))
    ma20 = _number(last.get("ma20"))
    ma60 = _number(last.get("ma60"))
    if None in (close, ma20, ma60):
        return "样本不足", "flat", "MA20/MA60 尚未形成完整样本"
    slope = None
    if "ma20" in df.columns and len(df) >= 6:
        prev = _number(df["ma20"].iloc[-6])
        slope = ma20 - prev if prev is not None else None
    if close > ma20 > ma60 and (slope is None or slope > 0):
        return "上升趋势", "bull", "价格位于 MA20、MA60 上方，且中期结构向上"
    if close < ma20 < ma60 and (slope is None or slope < 0):
        return "下降趋势", "bear", "价格位于 MA20、MA60 下方，且中期结构向下"
    return "震荡整理", "flat", "价格与中期均线尚未形成同向排列"


def _range_position(close: pd.Series, bars: int = 250) -> tuple[Optional[float], str]:
    values = pd.to_numeric(close, errors="coerce").dropna().tail(bars)
    if len(values) < 2:
        return None, "样本不足"
    low, high, last = float(values.min()), float(values.max()), float(values.iloc[-1])
    if high <= low:
        return 50.0, "区间收敛"
    pos = (last - low) / (high - low) * 100
    label = "高位区" if pos >= 75 else ("低位区" if pos <= 25 else "区间中部")
    return float(pos), label


def signal_alignment(score: dict) -> dict:
    """把组别覆盖度与多空一致程度合成 0~100 的“信号一致度”。

    该值不是上涨概率，也不是模型置信区间；只是用于回答“五组指标是否同向”。
    """
    bull = int(score.get("bull") or 0)
    bear = int(score.get("bear") or 0)
    total = bull + bear
    coverage = min(total / 5.0, 1.0)
    dominance = abs(bull - bear) / total if total else 0.0
    value = round((0.55 * coverage + 0.45 * dominance) * 100)
    label = "高" if value >= 75 else ("中" if value >= 50 else "低")
    direction = "偏多" if bull > bear else ("偏空" if bear > bull else "中性")
    return {
        "value": value,
        "label": label,
        "direction": direction,
        "coverage": total,
        "note": f"{total}/5 组有效，{bull} 多 / {bear} 空；不是涨跌概率",
    }


def build_snapshot(df: pd.DataFrame, report_result: Optional[dict] = None) -> dict:
    """生成首页决策仪表盘所需的稳定字段。"""
    if df is None or df.empty or "close" not in df.columns:
        return {"ok": False, "reason": "缺少有效收盘价"}
    close = pd.to_numeric(df["close"], errors="coerce")
    if close.dropna().empty:
        return {"ok": False, "reason": "缺少有效收盘价"}

    ret = close.pct_change().dropna().tail(20)
    annual_vol = (float(ret.std()) * math.sqrt(242) * 100
                  if len(ret) >= 10 else None)
    pos, pos_label = _range_position(close)
    trend, trend_tone, trend_note = _trend(df)
    score = (report_result or {}).get("score") or {}
    alignment = signal_alignment(score)
    last = df.iloc[-1]
    vol_ratio = _number(last.get("vol_ratio"))
    atr = _number(last.get("atr"))
    last_close = _number(last.get("close"))
    atr_pct = atr / last_close * 100 if atr is not None and last_close else None

    return {
        "ok": True,
        "returns": {n: _period_return(close, n) for n in (5, 20, 60, 250)},
        "trend": trend,
        "trend_tone": trend_tone,
        "trend_note": trend_note,
        "annual_vol_20": annual_vol,
        "max_drawdown_60": _max_drawdown(close, 60),
        "range_position": pos,
        "range_label": pos_label,
        "alignment": alignment,
        "vol_ratio": vol_ratio,
        "atr_pct": atr_pct,
        "n_bars": int(len(df)),
    }

