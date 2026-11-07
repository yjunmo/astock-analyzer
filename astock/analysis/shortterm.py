"""短线分析模块：涨停基因统计、次日溢价、短线信号评分与打板策略回测。

全部为已收盘K线的统计推演，纯计算、无网络，可离线测试。
统计口径说明：
- 涨停判定复用 data_fetcher 的 is_limit_up（不复权价四舍五入到分）；
- 「炸板」= 盘中最高触及涨停价但收盘未涨停（需要 raw_high 与 limit_up_px 列）；
- 涨停次日收益按复权价序列计算（close 口径一致，避免除权跳变）；
- 打板回测默认排除一字板（开盘即涨停，实盘买不进），T+1 次日开盘买入。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from astock.analysis.signals import BEAR, BULL, NEUTRAL, score

LIMIT_LOOKBACK = 60     # 涨停统计回看窗口（周期数）
HOLD_CHOICES = (1, 2)   # 打板回测持有周期（交易日）

# 短线信号阈值
VR_ACTIVE = 2.0         # 量比：短线资金活跃
VR_DEAD = 0.8           # 量比：缩量冷清
TURNOVER_HOT = 5.0      # 换手率%：活跃
TURNOVER_COLD = 1.5     # 换手率%：低迷
ATR_LOW = 1.0           # ATR占现价%下限：波动太小空间不足
ATR_HIGH = 5.0          # ATR占现价%上限：波动过大风险
RSI_CHASE = 85.0        # RSI6：追高风险
RSI_BOUNCE = 15.0       # RSI6：超跌反弹候选
MOM_STRONG = 8.0        # 5日累计涨幅%：短线强势
MOM_WEAK = -8.0         # 5日累计涨幅%：短线走弱


def _flags(df: pd.DataFrame, col: str) -> np.ndarray:
    if col in df.columns:
        return df[col].to_numpy(dtype=bool)
    return np.zeros(len(df), dtype=bool)


def _touch_flag(df: pd.DataFrame, up: np.ndarray) -> np.ndarray:
    """炸板：盘中触及涨停价但收盘未涨停。"""
    if "raw_high" in df.columns and "limit_up_px" in df.columns:
        touch = ((df["raw_high"].to_numpy(dtype=float)
                  + 1e-9 >= df["limit_up_px"].to_numpy(dtype=float)) & ~up)
        return touch & df["limit_up_px"].notna().to_numpy()
    return np.zeros(len(df), dtype=bool)


def limit_stats(df: pd.DataFrame, lookback: int = LIMIT_LOOKBACK) -> dict:
    """近 lookback 周期的涨停/炸板/连板统计。"""
    n = len(df)
    up = _flags(df, "is_limit_up")
    touch = _touch_flag(df, up)
    start = max(0, n - lookback)
    up_w = up[start:]
    touch_w = touch[start:]

    max_streak = streak = 0
    for v in up_w:
        streak = streak + 1 if v else 0
        max_streak = max(max_streak, streak)

    cnt_up = int(up_w.sum())
    cnt_touch = int(touch_w.sum())
    return {
        "lookback": int(n - start),
        "limit_ups": cnt_up,
        "touched": cnt_touch,
        "blow_rate": round(cnt_touch / max(cnt_up + cnt_touch, 1) * 100.0, 1),
        "max_streak": max_streak,
        "last_limit_idx": int(np.max(np.where(up)[0])) if up.any() else None,
    }


def next_day_stats(df: pd.DataFrame) -> dict:
    """涨停次日表现统计（仅统计次日K线已收盘的样本）。"""
    n = len(df)
    up = _flags(df, "is_limit_up")
    idxs = np.where(up)[0]
    rows = []
    for i in idxs:
        if i + 1 >= n:
            continue  # 最新一根涨停，次日未收盘，不计入
        o, h, c = (df["open"].iloc[i], df["high"].iloc[i], df["close"].iloc[i])
        o1, h1, c1 = (df["open"].iloc[i + 1], df["high"].iloc[i + 1],
                      df["close"].iloc[i + 1])
        if not (c > 0 and o1 > 0):
            continue
        one_word = float(h - o) < abs(o) * 1e-6  # 一字板
        rows.append({
            "date": pd.Timestamp(df["date"].iloc[i]).strftime("%Y-%m-%d"),
            "open_prem": o1 / c - 1.0,
            "high_prem": h1 / c - 1.0,
            "close_ret": c1 / c - 1.0,
            "one_word": one_word,
            "promote": bool(up[i + 1]) if i + 1 < n else False,  # 次日再涨停(一进二/连板)
        })
    if not rows:
        return {"n": 0}
    t = pd.DataFrame(rows)
    non_one = t[~t["one_word"]]
    close_ret = t["close_ret"]
    win = int((close_ret > 0).sum())
    return {
        "n": len(t),
        "open_prem_mean": round(float(t["open_prem"].mean()) * 100.0, 2),
        "open_prem_median": round(float(t["open_prem"].median()) * 100.0, 2),
        "high_prem_mean": round(float(t["high_prem"].mean()) * 100.0, 2),
        "close_ret_mean": round(float(close_ret.mean()) * 100.0, 2),
        "close_ret_median": round(float(close_ret.median()) * 100.0, 2),
        "win_rate": round(win / len(t) * 100.0, 1),
        "promote_rate": round(float(t["promote"].mean()) * 100.0, 1),
        "n_non_one": int(len(non_one)),
        "non_one_close_ret_mean": (
            round(float(non_one["close_ret"].mean()) * 100.0, 2)
            if len(non_one) else None),
        "rows": t,
    }


def _one_word(df: pd.DataFrame, i: int) -> bool:
    o, h = float(df["open"].iloc[i]), float(df["high"].iloc[i])
    return h - o < abs(o) * 1e-6


def limit_up_backtest(df: pd.DataFrame, hold: int = 1,
                      fee_rate: float = 2.5e-4,
                      exclude_one_word: bool = True) -> dict:
    """打板策略回测：涨停收盘日触发，次日开盘买入，持有 hold 个交易日收盘卖出。

    规则：T+1 次日开盘执行；排除一字板（买不进）；次日一字涨停也视为买不进；
    卖出日若为一字跌停（卖不出）顺延至下一可成交日收盘（最多顺延3日）。
    """
    if df is None or len(df) < LIMIT_LOOKBACK or hold not in HOLD_CHOICES:
        return {"ok": False, "reason": f"K线不足或持有周期不支持（支持{HOLD_CHOICES}）"}
    n = len(df)
    up = _flags(df, "is_limit_up")
    down = _flags(df, "is_limit_down")
    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)

    trades = []
    equity = 1.0
    equity_curve = []
    for i in range(1, n - 1):
        if not up[i - 1]:
            continue
        if exclude_one_word and _one_word(df, i - 1):
            continue
        if _one_word(df, i):  # 次日开盘即涨停，买不进
            continue
        entry = float(opens[i])
        if not (math.isfinite(entry) and entry > 0):
            continue
        # 卖出日：i + hold，若一字跌停顺延（最多 +3 日）
        exit_i = i + hold
        wait = 0
        while exit_i < n - 1 and wait < 3 and down[exit_i] and _one_word(df, exit_i):
            exit_i += 1
            wait += 1
        if exit_i >= n:
            continue
        exit_px = float(closes[exit_i])
        # 净收益率：卖出净得 / 买入成本 - 1（手续费单边计）
        ret = exit_px * (1.0 - fee_rate) / (entry * (1.0 + fee_rate)) - 1.0
        trades.append({
            "entry_date": pd.Timestamp(df["date"].iloc[i]).strftime("%Y-%m-%d"),
            "exit_date": pd.Timestamp(df["date"].iloc[exit_i]).strftime("%Y-%m-%d"),
            "return_pct": round(ret * 100.0, 2),
        })
        equity *= (1.0 + ret)
        equity_curve.append(equity)
    if not trades:
        return {"ok": True, "hold": hold, "n": 0, "trades": [],
                "metrics": None, "equity": []}
    rets = [t["return_pct"] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    eq = np.array(equity_curve)
    max_dd = float(((eq / np.maximum.accumulate(eq)) - 1.0).min())
    return {
        "ok": True,
        "hold": hold,
        "fee_rate": float(fee_rate),
        "n": len(trades),
        "trades": trades,
        "metrics": {
            "avg_return": round(float(np.mean(rets)), 2),
            "median_return": round(float(np.median(rets)), 2),
            "win_rate": round(wins / len(rets) * 100.0, 1),
            "total_return": round((equity - 1.0) * 100.0, 2),
            "max_drawdown": round(max_dd * 100.0, 2),
            "best": round(float(np.max(rets)), 2),
            "worst": round(float(np.min(rets)), 2),
        },
        "equity": [round(float(v), 6) for v in equity_curve],
    }


# ---------------------------------------------------------------- 短线信号

def shortterm_signals(df: pd.DataFrame) -> list:
    """短线专属五组信号（量能/动量/涨停基因/波动/情绪），供 score() 投票。"""
    items = []
    n = len(df)

    # 量能组
    if "vol_ratio" in df.columns and "turnover" in df.columns:
        last = df.iloc[-1]
        vr = last["vol_ratio"]
        to = last["turnover"]
        if pd.notna(vr) and pd.notna(to):
            if vr >= VR_ACTIVE and to >= TURNOVER_HOT:
                items.append((f"量比{vr:.2f} + 换手{to:.2f}%，短线资金活跃", BULL))
            elif vr >= VR_ACTIVE or to >= TURNOVER_HOT:
                items.append((f"量比{vr:.2f}/换手{to:.2f}%，活跃度中等", NEUTRAL))
            elif vr < VR_DEAD and to < TURNOVER_COLD:
                items.append((f"量比{vr:.2f} + 换手{to:.2f}%，缩量冷清，人气不足", BEAR))
            else:
                items.append((f"量比{vr:.2f}/换手{to:.2f}%，成交清淡", NEUTRAL))
        else:
            items.append(("量能数据缺失，暂不判断", NEUTRAL))
    else:
        items.append(("量能数据缺失，暂不判断", NEUTRAL))

    # 动量组（5日涨幅 + 短均线）
    if n >= 6 and "ma5" in df.columns and "ma10" in df.columns:
        last = df.iloc[-1]
        mom5 = (last["close"] / df["close"].iloc[-6] - 1.0) * 100.0
        if mom5 >= MOM_STRONG and last["close"] > last["ma5"]:
            items.append((f"5日涨幅{mom5:+.1f}%且站上MA5，短线强势", BULL))
        elif mom5 <= MOM_WEAK or last["close"] < last["ma10"]:
            items.append((f"5日涨幅{mom5:+.1f}%且跌破MA10，短线走弱，回避", BEAR))
        elif last["close"] > last["ma5"] > last["ma10"]:
            items.append(("短期均线多头排列，节奏未坏", NEUTRAL))
        else:
            items.append((f"5日涨幅{mom5:+.1f}%，方向未明", NEUTRAL))
    else:
        items.append(("样本不足，暂不判断", NEUTRAL))

    # 涨停基因组
    st_ = limit_stats(df, lookback=20)
    nd = next_day_stats(df)
    if st_["limit_ups"] == 0:
        items.append(("近20周期无涨停，无打板基因", NEUTRAL))
    else:
        bits = f"近20周期涨停{st_['limit_ups']}次"
        if st_["blow_rate"] >= 40 and st_["limit_ups"] + st_["touched"] >= 3:
            items.append((f"{bits}，炸板率{st_['blow_rate']}%，封板质量差", BEAR))
        elif nd.get("n") and nd["win_rate"] >= 60:
            items.append((f"{bits}，涨停次日胜率{nd['win_rate']}%，溢价基因好", BULL))
        elif nd.get("n") and nd["close_ret_mean"] is not None and nd["close_ret_mean"] < 0:
            items.append((f"{bits}，次日平均{nd['close_ret_mean']}%，承接差", BEAR))
        else:
            items.append((f"{bits}，封板质量一般", NEUTRAL))

    # 波动组（ATR 占比）
    if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]):
        atr = float(df["atr"].iloc[-1])
        close = float(df["close"].iloc[-1])
        pct = atr / close * 100.0 if close > 0 else None
        if pct is None:
            items.append(("波动数据异常，暂不判断", NEUTRAL))
        elif ATR_LOW <= pct <= ATR_HIGH:
            items.append((f"ATR占现价{pct:.1f}%，波动适中适合短线", BULL))
        elif pct > ATR_HIGH:
            items.append((f"ATR占现价{pct:.1f}%，波动过大，仓位需克制", BEAR))
        else:
            items.append((f"ATR占现价{pct:.1f}%，波动过小，短线空间有限", NEUTRAL))
    else:
        items.append(("波动数据缺失，暂不判断", NEUTRAL))

    # 情绪组（RSI/KDJ 极端区）
    if "rsi6" in df.columns and "kdj_j" in df.columns:
        last = df.iloc[-1]
        r6, j = last["rsi6"], last["kdj_j"]
        if pd.notna(r6) and pd.notna(j):
            if r6 >= RSI_CHASE or j > 100:
                items.append((f"RSI6={r6:.0f} J={j:.0f}，情绪过热，追高易套", BEAR))
            elif r6 <= RSI_BOUNCE:
                items.append((f"RSI6={r6:.0f}，超跌，关注反抽机会", BULL))
            else:
                items.append((f"RSI6={r6:.0f} J={j:.0f}，情绪中性", NEUTRAL))
        else:
            items.append(("情绪指标缺失，暂不判断", NEUTRAL))
    else:
        items.append(("情绪指标缺失，暂不判断", NEUTRAL))

    return [{"name": name, "items": [it]} for name, it in zip(
        ("量能活跃度", "短线动量", "涨停基因", "波动空间", "情绪位置"), items)]


def build_shortterm_report(df: pd.DataFrame) -> dict:
    """短线报告组装：统计 + 信号评分 + 打板回测（持有1日/2日）。"""
    st_ = limit_stats(df)
    nd = next_day_stats(df)
    groups = shortterm_signals(df)
    s = score(groups)
    bt1 = limit_up_backtest(df, hold=1)
    bt2 = limit_up_backtest(df, hold=2)
    return {"stats": st_, "next_day": nd, "groups": groups, "score": s,
            "backtest": {1: bt1, 2: bt2}}


def shortterm_text(rep: Optional[dict]) -> str:
    """短线结论紧凑文本（供注入 AI 提示词）。"""
    if not rep:
        return "（短线分析数据缺失）"
    st_ = rep["stats"]
    nd = rep["next_day"]
    s = rep["score"]
    lines = [
        f"近{st_['lookback']}周期：涨停 {st_['limit_ups']} 次 · 炸板率 {st_['blow_rate']}%"
        f" · 最高连板 {st_['max_streak']} 板",
    ]
    if nd.get("n"):
        lines.append(
            f"涨停次日（样本{nd['n']}次）：开盘溢价均值 {nd['open_prem_mean']:+.2f}% · "
            f"最高溢价均值 {nd['high_prem_mean']:+.2f}% · 收盘均值 {nd['close_ret_mean']:+.2f}%"
            f" · 胜率 {nd['win_rate']}% · 晋级率 {nd['promote_rate']}%")
    else:
        lines.append("涨停次日统计：样本不足")
    lines.append(f"短线信号评分：{s['verdict']}（多{s['bull']} : 空{s['bear']}）")
    for hold in HOLD_CHOICES:
        bt = rep["backtest"].get(hold) or {}
        if bt.get("ok") and bt.get("n"):
            m = bt["metrics"]
            lines.append(f"打板持有{hold}日回测：{bt['n']}笔 · 平均{m['avg_return']:+.2f}%"
                         f" · 胜率{m['win_rate']}% · 累计{m['total_return']:+.2f}%"
                         f" · 最大回撤{m['max_drawdown']}%")
        else:
            lines.append(f"打板持有{hold}日回测：样本不足")
    lines.append("以上为已收盘K线统计推演，涨停次日统计基于历史样本，不代表未来。")
    return "\n".join(lines)
