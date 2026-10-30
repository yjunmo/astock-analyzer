"""历史回测引擎（纯计算，无网络请求，可离线测试）。

策略：
- score：复用信号投票评分——tone 转多(BULL)次日开仓买入、转空(BEAR)次日清仓；
- ma_cross：MA5/MA20 金叉次日买入、死叉次日卖出。

A股规则适配：
- T+1：信号仅基于已收盘K线，下一根K线开盘价执行；
- 涨跌停：涨停日不可买入、跌停日不可卖出（挂单无法成交），跳过该次执行；
- 无个股做空工具：空仓期持有现金，不模拟卖空；
- 手续费按单边费率计（默认万2.5），不计滑点与印花税（可配置扩展）。

输入 df 需已含指标列（indicators.compute_all + 涨跌停标志），
score 策略另需 vol_ratio/is_limit_up/is_limit_down。
输出仅代表历史推演，不代表未来收益。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from signals import (BEAR, BOLL_SLOPE_WINDOW, BULL, KDJ_OVERBOUGHT,
                     KDJ_OVERSOLD, NEUTRAL, SCORE_MIN_VOTES, SCORE_STRONG,
                     SCORE_WEAK, VOL_CONFIRM)

STRATEGIES = ("score", "ma_cross")
DEFAULT_FEE_RATE = 2.5e-4
WARMUP = 60                # 评分策略指标预热根数
MIN_BARS = WARMUP + 5


def _col_arr(df: pd.DataFrame, col: str) -> np.ndarray:
    return (df[col].to_numpy(dtype=float)
            if col in df.columns else np.full(len(df), np.nan))


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    if k >= len(a) or k <= 0:
        return out
    out[k:] = a[:-k]
    return out


def _crosses(a: np.ndarray, b: np.ndarray):
    """返回 (golden, dead) 布尔数组，与 signals._crossed 逐根等价。"""
    d_ = a - b
    prev, curr = d_[:-1], d_[1:]
    valid = np.isfinite(prev) & np.isfinite(curr)
    golden = np.concatenate([[False], valid & (prev < 0) & (curr > 0)])
    dead = np.concatenate([[False], valid & (prev > 0) & (curr < 0)])
    return golden, dead


def score_tone_series(df: pd.DataFrame) -> np.ndarray:
    """逐根K线复现 signals.collect_signals+score 的评分语调（向量化）。

    与 signals 模块共享阈值常量；仅在指标列已预计算的前提下等价。
    返回数组元素为 BULL/BEAR/NEUTRAL（与 signals.score 语调一致）。
    """
    n = len(df)
    close = _col_arr(df, "close")
    ma5, ma20, ma60 = (_col_arr(df, "ma5"), _col_arr(df, "ma20"),
                       _col_arr(df, "ma60"))
    dif, dea = _col_arr(df, "dif"), _col_arr(df, "dea")
    k, d, j = _col_arr(df, "kdj_k"), _col_arr(df, "kdj_d"), _col_arr(df, "kdj_j")
    r6, r12, r24 = (_col_arr(df, "rsi6"), _col_arr(df, "rsi12"),
                    _col_arr(df, "rsi24"))
    up, low, mid = (_col_arr(df, "boll_up"), _col_arr(df, "boll_low"),
                    _col_arr(df, "boll_mid"))
    vol = _col_arr(df, "vol_ratio")
    limit_up = np.nan_to_num(_col_arr(df, "is_limit_up"), nan=0.0) > 0
    limit_dn = np.nan_to_num(_col_arr(df, "is_limit_down"), nan=0.0) > 0

    # _confirm_volume：量比缺失视为不否决；量比 < 1.2 时多头降级中性
    vol_ok = (~np.isfinite(vol)) | (vol >= VOL_CONFIRM)

    def bull(cond):  # 多头项：量能确认 + 涨停当日不可买
        return (cond & vol_ok) & (~limit_up)

    def bear(cond):  # 空头项：跌停当日不可卖
        return cond & (~limit_dn)

    # ---- 均线组：收盘/MA5、MA5/MA20 交叉 + 排列
    g1, g2 = _crosses(close, ma5)
    ma_bull = bull(g1).astype(int)
    ma_bear = bear(g2).astype(int)
    g1, g2 = _crosses(ma5, ma20)
    ma_bull += bull(g1)
    ma_bear += bear(g2)
    align = np.isfinite(ma5) & np.isfinite(ma20) & np.isfinite(ma60)
    ma_bull += align & (ma5 > ma20) & (ma20 > ma60)
    ma_bear += align & (ma5 < ma20) & (ma20 < ma60)

    # ---- MACD 组：DIF/DEA 交叉 + 零轴状态
    g1, g2 = _crosses(dif, dea)
    macd_bull = bull(g1).astype(int)
    macd_bear = bear(g2).astype(int)
    macd_bull += (~g1) & (~g2) & (dif > dea) & (dif > 0)
    macd_bear += (~g1) & (~g2) & (dif < dea) & (dif < 0)
    macd_bull += (dif > 0) & (dea > 0)
    macd_bear += (dif < 0) & (dea < 0)

    # ---- KDJ 组：K/D 交叉 + 超买超卖
    g1, g2 = _crosses(k, d)
    kdj_bull = bull(g1).astype(int)
    kdj_bear = bear(g2).astype(int)
    kdj_bear += (j > 100) | ((k > KDJ_OVERBOUGHT) & (d > KDJ_OVERBOUGHT))
    kdj_bull += (j < 0) | ((k < KDJ_OVERSOLD) & (d < KDJ_OVERSOLD))

    # ---- RSI 组：超买超卖 + 排序
    rsi_bull = (r6 < 20).astype(int)
    rsi_bear = (r6 > 80).astype(int)
    rsi_bull += (r6 > r12) & (r12 > r24)
    rsi_bear += (r6 < r12) & (r12 < r24)

    # ---- BOLL 组：轨线突破 + 中轨斜率（iloc[-5] 即倒数第5根，间隔4根）
    pc, pu, pl = _shift(close, 1), _shift(up, 1), _shift(low, 1)
    boll_bull = bull((pc <= pu) & (pu < close)).astype(int)
    boll_bear = bear((pc >= pl) & (pl > close)).astype(int)
    mid5 = _shift(mid, BOLL_SLOPE_WINDOW - 1)
    slope = np.isfinite(mid) & np.isfinite(mid5)
    boll_bull += slope & (mid > mid5)
    boll_bear += slope & (mid < mid5)

    # ---- 组别投票 + 评分阈值（与 signals.score 一致）
    votes = np.vstack([ma_bull - ma_bear, macd_bull - macd_bear,
                       kdj_bull - kdj_bear, rsi_bull - rsi_bear,
                       boll_bull - boll_bear])
    bull_g = (votes > 0).sum(axis=0).astype(float)
    bear_g = (votes < 0).sum(axis=0).astype(float)
    total = bull_g + bear_g
    ratio = np.where(total > 0, bull_g / np.maximum(total, 1), 0.5)

    tones = np.full(n, NEUTRAL, dtype=object)
    valid = total >= SCORE_MIN_VOTES
    tones[valid & (ratio >= SCORE_STRONG)] = BULL
    tones[valid & (ratio >= SCORE_WEAK) & (ratio < SCORE_STRONG)] = BULL
    tones[valid & (ratio <= 1.0 - SCORE_STRONG)] = BEAR
    tones[valid & (ratio <= 1.0 - SCORE_WEAK) & (ratio > 1.0 - SCORE_STRONG)] = BEAR
    return tones


def _signal_series(df: pd.DataFrame, strategy: str, warmup: int) -> tuple:
    """逐根已收盘K线给出买卖意图，返回 (buy_idx, sell_idx) 布尔数组。"""
    n = len(df)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    if strategy == "ma_cross":
        if "ma5" not in df.columns or "ma20" not in df.columns:
            return buy, sell
        golden, dead = _crosses(df["ma5"].to_numpy(dtype=float),
                                df["ma20"].to_numpy(dtype=float))
        buy, sell = golden, dead
    else:  # score：向量化评分语调，预热期强制中性
        tones = score_tone_series(df)
        for i in range(int(warmup), n):
            if tones[i] == BULL:
                buy[i] = True
            elif tones[i] == BEAR:
                sell[i] = True
    return buy, sell


def _to_date(dt) -> str:
    try:
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "-"


def _run_loop(df: pd.DataFrame, buy: np.ndarray, sell: np.ndarray,
              fee_rate: float) -> tuple:
    """按 T+1 与涨跌停过滤模拟执行，返回 (equity 数组, trades 列表)。

    执行可行性按开盘价与涨跌停价比较（开盘即触及涨停买不进/跌停卖不出），
    避免使用第 i 根收盘后才确定的涨停标志造成前视偏差；无涨跌停价数据时
    退回旧口径（用收盘涨停标志近似）。
    """
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float) if "open" in df.columns else close
    limit_up = (df["is_limit_up"].to_numpy(dtype=bool)
                if "is_limit_up" in df.columns else np.zeros(n, dtype=bool))
    limit_dn = (df["is_limit_down"].to_numpy(dtype=bool)
                if "is_limit_down" in df.columns else np.zeros(n, dtype=bool))
    up_px = (df["limit_up_px"].to_numpy(dtype=float)
             if "limit_up_px" in df.columns else np.full(n, np.nan))
    dn_px = (df["limit_down_px"].to_numpy(dtype=float)
             if "limit_down_px" in df.columns else np.full(n, np.nan))

    def _can_buy(i: int, px: float) -> bool:
        if math.isfinite(up_px[i]):
            return px + 1e-9 < up_px[i]
        return not bool(limit_up[i])

    def _can_sell(i: int, px: float) -> bool:
        if math.isfinite(dn_px[i]):
            return px - 1e-9 > dn_px[i]
        return not bool(limit_dn[i])

    cash = 1.0
    shares = 0.0
    equity = np.empty(n, dtype=float)
    trades: list = []
    open_trade = None

    for i in range(n):
        # 第 i 根K线收盘后信号 → 第 i+1 根开盘执行；最后一根无法执行
        if i > 0:
            if buy[i - 1] and shares == 0:
                px = float(open_[i])
                if math.isfinite(px) and px > 0 and _can_buy(i, px):
                    shares = cash * (1.0 - fee_rate) / px
                    cash = 0.0
                    open_trade = {"entry_date": _to_date(df["date"].iloc[i]),
                                  "entry_price": px}
            elif sell[i - 1] and shares > 0:
                px = float(open_[i])
                if math.isfinite(px) and px > 0 and _can_sell(i, px):
                    cash = shares * px * (1.0 - fee_rate)
                    shares = 0.0
                    if open_trade:
                        # 双边手续费计入明细收益，与净值曲线口径一致
                        ret = (1.0 - fee_rate) ** 2 * px / open_trade["entry_price"] - 1.0
                        trades.append({
                            "entry_date": open_trade["entry_date"],
                            "entry_price": round(open_trade["entry_price"], 3),
                            "exit_date": _to_date(df["date"].iloc[i]),
                            "exit_price": round(px, 3),
                            "return_pct": round(ret * 100.0, 2),
                        })
                    open_trade = None
        equity[i] = cash + shares * float(close[i])

    # 期末仍持仓：按最后收盘价市值计（不扣卖出费用，仅作展示）
    if open_trade and shares > 0:
        last_px = float(close[-1])
        ret = (1.0 - fee_rate) * last_px / open_trade["entry_price"] - 1.0
        open_trade.update({"exit_date": _to_date(df["date"].iloc[-1]) + "（持仓中）",
                           "exit_price": round(last_px, 3),
                           "return_pct": round(ret * 100.0, 2),
                           "open": True})
        trades.append(open_trade)
    return equity, trades


def _metrics(df: pd.DataFrame, equity: np.ndarray, trades: list) -> dict:
    n = len(equity)
    final = float(equity[-1])
    first_close = float(df["close"].iloc[0])
    last_close = float(df["close"].iloc[-1])
    bench = (df["close"].to_numpy(dtype=float) / first_close
             if first_close > 0 else np.ones(n))

    peak = float(np.maximum.accumulate(equity).max())
    max_dd = float(((equity / np.maximum.accumulate(equity)) - 1.0).min()) if n else 0.0

    days = max((df["date"].iloc[-1] - df["date"].iloc[0]).days, 1)
    annual = (final ** (365.0 / days) - 1.0) if final > 0 else -1.0

    closed = [t for t in trades if not t.get("open")]
    wins = sum(1 for t in closed if t["return_pct"] > 0)
    rets = [t["return_pct"] for t in closed]

    return {
        "total_return": round((final - 1.0) * 100.0, 2),
        "annual_return": round(annual * 100.0, 2),
        "max_drawdown": round(max_dd * 100.0, 2),
        "buy_hold_return": round((last_close / first_close - 1.0) * 100.0, 2)
        if first_close > 0 else 0.0,
        "n_trades": len(trades),
        "win_rate": round(wins / len(closed) * 100.0, 1) if closed else None,
        "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
        "peak_value": round(peak, 4),
        "days": days,
        "bench": bench,
        "equity": equity,
    }


def run_backtest(df: pd.DataFrame, strategy: str = "score",
                 fee_rate: float = DEFAULT_FEE_RATE,
                 warmup: int = WARMUP) -> dict:
    """执行回测。df 须含 date/open/close 及策略所需指标列。"""
    if df is None or len(df) < MIN_BARS:
        return {"ok": False,
                "reason": f"K线不足（需要≥{MIN_BARS}根已收盘K线）"}
    if strategy not in STRATEGIES:
        return {"ok": False, "reason": f"未知策略：{strategy}"}
    if not (0.0 <= float(fee_rate) < 1.0):
        return {"ok": False, "reason": "手续费费率须在 [0, 1) 区间（如 0.00025）"}

    buy, sell = _signal_series(df, strategy, int(warmup))
    equity, trades = _run_loop(df, buy, sell, float(fee_rate))
    m = _metrics(df, equity, trades)
    return {
        "ok": True,
        "strategy": strategy,
        "fee_rate": float(fee_rate),
        "warmup": int(warmup),
        "metrics": m,
        "trades": trades,
        "equity_df": pd.DataFrame({
            "date": df["date"].reset_index(drop=True),
            "strategy": equity,
            "benchmark": m["bench"],
            "drawdown": equity / np.maximum.accumulate(equity) - 1.0,
        }),
    }


def trades_df(bt: dict) -> pd.DataFrame:
    """回测交易明细 DataFrame（供 UI 表格）。"""
    if not bt.get("ok") or not bt.get("trades"):
        return pd.DataFrame()
    return pd.DataFrame(bt["trades"])


def backtest_text(bt: dict, name: str = "") -> str:
    """回测结果的紧凑文本（供注入 AI 提示词）。"""
    if not bt.get("ok"):
        return f"（回测不可用：{bt.get('reason', '未知原因')}）"
    m = bt["metrics"]
    lines = [
        f"策略：{'信号评分' if bt['strategy'] == 'score' else 'MA5/MA20双均线'}",
        f"总收益 {m['total_return']:+.2f}%（买入持有 {m['buy_hold_return']:+.2f}%）",
        f"年化 {m['annual_return']:+.2f}% · 最大回撤 {m['max_drawdown']:.2f}%",
        f"交易 {m['n_trades']} 次 · 胜率 "
        + ("-" if m["win_rate"] is None else f"{m['win_rate']:.1f}%"),
    ]
    if name:
        lines.insert(0, f"【{name} 历史回测（{bt['equity_df']['date'].iloc[0]} ~ "
                        f"{bt['equity_df']['date'].iloc[-1]}）】")
    lines.append("回测为历史数据推演，不代表未来收益；含手续费"
                 f"单边{bt['fee_rate'] * 10000:.1f}/万（未计印花税/滑点）。")
    return "\n".join(lines)
