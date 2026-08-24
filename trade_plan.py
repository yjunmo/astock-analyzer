"""买卖参考价位推荐。

思路：以已收盘K线上的近端支撑/压力（均线、布林轨道、近 N 期摆动高低点）
为锚，用 ATR(14) 度量缓冲距离，按多空基调给出低吸区、突破确认价、
目标位、止损/离场线等参考价位，全部四舍五入到分。

仅为技术位推算，不构成投资建议；A股 T+1 且无个股做空工具，
空头建议一律为"反弹减仓"，最早下一交易日执行。
"""
from typing import Optional

import numpy as np
import pandas as pd

from signals import BEAR, BULL

SWING_WINDOW = 10        # 摆动高低点回看窗口（不含最新一根）
MIN_BARS = 40            # 参与计算所需最少周期数
ATR_COL = "atr"

ENTRY_BUFFER_ATR = 0.35  # 支撑/压力两侧的低吸/减仓缓冲
BREAK_BUFFER_ATR = 0.15  # 突破确认缓冲
STOP_BUFFER_ATR = 0.80   # 止损距离
EXIT_BUFFER_ATR = 0.30   # 空头破位离场缓冲
TARGET_EXTEND_ATR = 1.00 # 第二目标外推距离

_REQUIRED = ("close", "high", "low", "ma5", "ma10", "ma20",
             "boll_mid", "boll_up", ATR_COL)


def _finite(v) -> bool:
    return v is not None and pd.notna(v) and np.isfinite(v)


def _round_px(value) -> Optional[float]:
    if not _finite(value):
        return None
    return float(np.floor(value * 100.0 + 0.5 + 1e-9) / 100.0)


def _fmt(px) -> str:
    p = _round_px(px)
    return f"{p:.2f}" if p is not None else "-"


def _pick(levels, price: float, below: bool) -> Optional[float]:
    """取 levels 中位于 price 下侧的最近值（below=True）或上侧最近值。"""
    vals = [float(lv) for lv in levels if _finite(lv)]
    vals = [v for v in vals if (v <= price if below else v >= price)]
    if not vals:
        return None
    return max(vals) if below else min(vals)


def _zone(center: float, atr_v: float):
    return center - ENTRY_BUFFER_ATR * atr_v, center + ENTRY_BUFFER_ATR * atr_v


def build_trade_plan(df: pd.DataFrame, tone: str) -> Optional[dict]:
    """按多空基调生成参考价位；数据不足或关键指标缺失时返回 None。"""
    if df is None or len(df) < MIN_BARS:
        return None
    if any(c not in df.columns for c in _REQUIRED):
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    atr_v = float(last[ATR_COL])
    if not (_finite(close) and close > 0 and _finite(atr_v) and atr_v > 0):
        return None

    window = slice(-(SWING_WINDOW + 1), -1)
    swing_high = float(df["high"].iloc[window].max())
    swing_low = float(df["low"].iloc[window].min())

    ma_levels = [last.get(c) for c in ("ma5", "ma10", "ma20", "boll_mid")]
    support = _pick(ma_levels + [swing_low], close, below=True)
    resistance = _pick(ma_levels + [last.get("boll_up"), swing_high], close, below=False)

    sup_fallback = close - atr_v
    res_fallback = close + atr_v

    plan = {"tone": tone, "close": round(close, 2), "atr": round(atr_v, 2)}

    if tone == BULL:
        sup = support if support is not None else sup_fallback
        buy_lo, buy_hi = _zone(sup, atr_v)
        stop = min(sup - STOP_BUFFER_ATR * atr_v, buy_lo - 0.01)
        base_high = swing_high if swing_high > close else close
        breakout = max(base_high + BREAK_BUFFER_ATR * atr_v, close + BREAK_BUFFER_ATR * atr_v)
        t1 = resistance if resistance is not None else res_fallback
        t1 = max(t1, buy_hi + atr_v)
        t2 = t1 + TARGET_EXTEND_ATR * atr_v
        rr = (t1 - buy_hi) / (buy_hi - stop) if buy_hi > stop else None

        plan.update({
            "buy_low": _round_px(buy_lo), "buy_high": _round_px(buy_hi),
            "breakout": _round_px(breakout),
            "target1": _round_px(t1), "target2": _round_px(t2),
            "stop": _round_px(stop),
        })
        rr_txt = f"，盈亏比约 {rr:.1f}" if rr is not None else ""
        plan["cards"] = [
            ("低吸参考区", f"{_fmt(buy_lo)} ~ {_fmt(buy_hi)}"),
            ("突破确认价", f">= {_fmt(breakout)}"),
            ("第一目标", _fmt(t1)),
            ("第二目标", _fmt(t2)),
            ("止损参考", _fmt(stop)),
        ]
        plan["lines"] = [
            f"[低吸] 回踩 {_fmt(buy_lo)}~{_fmt(buy_hi)}（近端支撑±{ENTRY_BUFFER_ATR}ATR）"
            f"可分批关注，收盘跌破 {_fmt(stop)} 止损",
            f"[突破] 放量站上 {_fmt(breakout)} 可视作有效突破确认",
            f"[目标] 第一目标 {_fmt(t1)}，第二目标 {_fmt(t2)}{rr_txt}",
        ]
    elif tone == BEAR:
        res = resistance if resistance is not None else res_fallback
        trim_lo, trim_hi = _zone(res, atr_v)
        exit_base = support if support is not None else sup_fallback
        exit_line = exit_base - EXIT_BUFFER_ATR * atr_v
        down_watch = exit_base - TARGET_EXTEND_ATR * atr_v

        plan.update({
            "trim_low": _round_px(trim_lo), "trim_high": _round_px(trim_hi),
            "exit_line": _round_px(exit_line),
            "down_watch": _round_px(down_watch),
        })
        plan["cards"] = [
            ("反弹减仓区", f"{_fmt(trim_lo)} ~ {_fmt(trim_hi)}"),
            ("破位离场线", f"<= {_fmt(exit_line)}"),
            ("下方观察位", _fmt(down_watch)),
        ]
        plan["lines"] = [
            f"[减仓] 反弹至 {_fmt(trim_lo)}~{_fmt(trim_hi)}（近端压力±{ENTRY_BUFFER_ATR}ATR）"
            f"分批减仓，A股无做空工具，不做空头开仓假设",
            f"[警戒] 收盘跌破 {_fmt(exit_line)} 建议进一步降低仓位",
            f"[观察] 下方先看 {_fmt(down_watch)} 附近能否承接",
        ]
    else:
        lo_c = support if support is not None else sup_fallback
        hi_c = resistance if resistance is not None else res_fallback
        buy_lo, buy_hi = _zone(lo_c, atr_v)
        trim_lo, trim_hi = _zone(hi_c, atr_v)
        base_high = swing_high if swing_high > close else close
        breakout = max(base_high + BREAK_BUFFER_ATR * atr_v, close + BREAK_BUFFER_ATR * atr_v)
        exit_line = lo_c - EXIT_BUFFER_ATR * atr_v

        plan.update({
            "buy_low": _round_px(buy_lo), "buy_high": _round_px(buy_hi),
            "trim_low": _round_px(trim_lo), "trim_high": _round_px(trim_hi),
            "breakout": _round_px(breakout),
            "exit_line": _round_px(exit_line),
        })
        plan["cards"] = [
            ("区间低吸区", f"{_fmt(buy_lo)} ~ {_fmt(buy_hi)}"),
            ("区间高抛区", f"{_fmt(trim_lo)} ~ {_fmt(trim_hi)}"),
            ("向上突破确认", f">= {_fmt(breakout)}"),
            ("破位离场线", f"<= {_fmt(exit_line)}"),
        ]
        plan["lines"] = [
            f"[低吸] 回踩 {_fmt(buy_lo)}~{_fmt(buy_hi)} 关注企稳信号",
            f"[高抛] 反弹至 {_fmt(trim_lo)}~{_fmt(trim_hi)} 可逢高了结",
            f"[变盘] 向上放量站上 {_fmt(breakout)} 转多头思路；"
            f"收盘跌破 {_fmt(exit_line)} 按空头纪律离场",
        ]

    plan["note"] = (
        f"基于已收盘K线技术位推算（ATR14={plan['atr']}），四舍五入到分；"
        "T+1 下最早下一交易日执行，仅供研究参考，不构成投资建议。"
    )
    return plan
