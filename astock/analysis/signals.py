import numpy as np
import pandas as pd

BULL = "bullish"
BEAR = "bearish"
NEUTRAL = "neutral"

VOL_CONFIRM = 1.2        # 多头信号放量确认阈值（量比，基准为上一周期均量）
VOL_SHRINK = 0.8         # 缩量判定阈值
KDJ_OVERBOUGHT = 80.0
KDJ_OVERSOLD = 20.0
KDJ_GOLD_ZONE = 25.0     # 金叉时 D 处于该值之下视为超卖区金叉
KDJ_DEAD_ZONE = 75.0     # 死叉时 D 处于该值之上视为超买区死叉
RSI_OVERBOUGHT = 80.0
RSI_OVERSOLD = 20.0
BOLL_SLOPE_WINDOW = 5    # 布林中轨斜率回看窗口（周期数）

SCORE_STRONG = 0.70      # 组别比 ≥ 0.70：明显占优
SCORE_WEAK = 0.55        # 组别比 ≥ 0.55 且 < 0.70：略占优
SCORE_MIN_VOTES = 2      # 有效投票组数下限，不足则观望


def _finite(*vals) -> bool:
    return all(pd.notna(v) and np.isfinite(v) for v in vals)


def _last_finite(df: pd.DataFrame, cols, n_last: int = 2) -> bool:
    if len(df) < n_last:
        return False
    tail = df.iloc[-n_last:]
    for c in cols:
        if c not in df.columns:
            return False
        vals = tail[c]
        if vals.isna().any():
            return False
        if not np.isfinite(vals.to_numpy(dtype=float)).all():
            return False
    return True


def _insufficient(name: str) -> dict:
    return {"name": name, "items": [("样本不足，暂不判断", NEUTRAL)]}


def flag_value(row, name: str) -> bool:
    """读取行内布尔标志列，缺失/NaN 一律 False。report 模块复用。"""
    if name not in getattr(row, "index", []):
        return False
    v = row[name]
    if pd.isna(v):
        return False
    return bool(v)


def _cross_kind(prev: float, curr: float):
    if prev < 0 < curr:
        return "golden"
    if prev > 0 > curr:
        return "dead"
    return None


def _crossed(fast: pd.Series, slow: pd.Series):
    diff = fast - slow
    if len(diff) < 2 or pd.isna(diff.iloc[-1]) or pd.isna(diff.iloc[-2]):
        return None
    return _cross_kind(float(diff.iloc[-2]), float(diff.iloc[-1]))


def _cross_events(fast: pd.Series, slow: pd.Series, dates: pd.Series, lookback: int):
    diff = fast - slow
    events = []
    start = max(1, len(diff) - lookback)
    for i in range(start, len(diff)):
        if pd.isna(diff.iloc[i]) or pd.isna(diff.iloc[i - 1]):
            continue
        kind = _cross_kind(float(diff.iloc[i - 1]), float(diff.iloc[i]))
        if kind == "golden":
            events.append((dates.iloc[i], "golden"))
        elif kind == "dead":
            events.append((dates.iloc[i], "dead"))
    return events


def _confirm_volume(df: pd.DataFrame, text: str, status: str):
    if status != BULL or "vol_ratio" not in df.columns:
        return text, status
    r = df["vol_ratio"].iloc[-1]
    if pd.isna(r) or not np.isfinite(r):
        return text, status
    if r < VOL_CONFIRM:
        return f"{text}（量能不足 量比={r:.2f}，暂不确认）", NEUTRAL
    return text, status


def _bear_volume_note(df: pd.DataFrame) -> str:
    """空头信号的量能标注：区分放量下杀与缩量阴跌，不改变信号方向。"""
    if "vol_ratio" not in df.columns:
        return ""
    r = df["vol_ratio"].iloc[-1]
    if pd.isna(r) or not np.isfinite(r):
        return ""
    if r >= VOL_CONFIRM:
        return "，放量下杀"
    if r < VOL_SHRINK:
        return "，缩量阴跌"
    return ""


def _apply_exec_filter(df: pd.DataFrame, text: str, status: str, need_volume: bool = False):
    if need_volume:
        text, status = _confirm_volume(df, text, status)
    last = df.iloc[-1]
    if status == BULL and flag_value(last, "is_limit_up"):
        return f"{text}（涨停封板，当日不可买，下一交易日观察）", NEUTRAL
    if status == BEAR and flag_value(last, "is_limit_down"):
        return f"{text}（跌停封死，当日可能无法卖出，减仓/回避）", NEUTRAL
    return text, status


def ma_signals(df: pd.DataFrame) -> dict:
    name = "均线系统 MA"
    if not _last_finite(df, ["close", "ma5"], 2):
        return _insufficient(name)
    last = df.iloc[-1]
    items = []

    cross5 = _crossed(df["close"], df["ma5"])
    if cross5 == "golden":
        items.append(_apply_exec_filter(df, "收盘价金叉站上MA5", BULL, need_volume=True))
    elif cross5 == "dead":
        items.append(_apply_exec_filter(df, "收盘价死叉跌破MA5，减仓/回避" + _bear_volume_note(df), BEAR))
    else:
        side = "上方" if last["close"] > last["ma5"] else "下方"
        items.append((f"收盘价运行于MA5{side}", NEUTRAL))

    if not _last_finite(df, ["ma5", "ma20"], 2):
        items.append(("MA5/MA20 样本不足，暂不判断", NEUTRAL))
    else:
        cross = _crossed(df["ma5"], df["ma20"])
        if cross == "golden":
            items.append(_apply_exec_filter(df, "MA5金叉MA20", BULL, need_volume=True))
        elif cross == "dead":
            items.append(_apply_exec_filter(df, "MA5死叉MA20，减仓/回避" + _bear_volume_note(df), BEAR))
        else:
            rel = ">" if last["ma5"] > last["ma20"] else "<"
            items.append((f"MA5{rel}MA20，趋势延续中", NEUTRAL))

    if not _last_finite(df, ["ma5", "ma20", "ma60"], 1):
        items.append(("均线排列样本不足，暂不判断", NEUTRAL))
    elif last["ma5"] > last["ma20"] > last["ma60"]:
        items.append(("均线多头排列 MA5>MA20>MA60", BULL))
    elif last["ma5"] < last["ma20"] < last["ma60"]:
        items.append(("均线空头排列 MA5<MA20<MA60，减仓/回避", BEAR))
    else:
        items.append(("均线交织，方向不明", NEUTRAL))

    return {"name": name, "items": items}


def macd_signals(df: pd.DataFrame) -> dict:
    name = "指数平滑异同 MACD"
    if not _last_finite(df, ["dif", "dea", "macd"], 2):
        return _insufficient(name)
    last = df.iloc[-1]
    items = []
    cross = _crossed(df["dif"], df["dea"])
    zone = "零轴上方" if last["dif"] > 0 else "零轴下方"
    if cross == "golden":
        items.append(_apply_exec_filter(df, f"DIF金叉DEA({zone})", BULL, need_volume=True))
    elif cross == "dead":
        items.append(_apply_exec_filter(df, f"DIF死叉DEA({zone})，减仓/回避" + _bear_volume_note(df), BEAR))
    else:
        rel = ">" if last["dif"] > last["dea"] else "<"
        tone = NEUTRAL
        if last["dif"] > last["dea"] and last["dif"] > 0:
            tone = BULL
        elif last["dif"] < last["dea"] and last["dif"] < 0:
            tone = BEAR
        if last["dif"] > 0:
            txt = f"DIF{rel}DEA，零轴上趋势延续"
        else:
            txt = f"DIF{rel}DEA，零轴下（反弹/回补结构，非多头趋势）"
        if tone == BEAR:
            txt += "，减仓/回避"
        items.append((txt, tone))

    prev_macd = df["macd"].iloc[-2]
    if last["macd"] > 0 and prev_macd > 0 and last["macd"] < prev_macd:
        items.append(("红柱缩短，上涨动能减弱", NEUTRAL))
    elif last["macd"] < 0 and prev_macd < 0 and last["macd"] > prev_macd:
        items.append(("绿柱缩短，下跌动能减弱", NEUTRAL))

    if last["dif"] > 0 and last["dea"] > 0:
        items.append(("双线处零轴上方，强势区", BULL))
    elif last["dif"] < 0 and last["dea"] < 0:
        items.append(("双线处零轴下方，弱势区，减仓/回避", BEAR))

    return {"name": name, "items": items}


def kdj_signals(df: pd.DataFrame) -> dict:
    name = "随机指标 KDJ"
    if not _last_finite(df, ["kdj_k", "kdj_d", "kdj_j"], 2):
        return _insufficient(name)
    last = df.iloc[-1]
    k, d, j = last["kdj_k"], last["kdj_d"], last["kdj_j"]
    items = []
    cross = _crossed(df["kdj_k"], df["kdj_d"])
    if cross == "golden":
        tag = "（超卖区）" if d < KDJ_GOLD_ZONE else ""
        items.append(_apply_exec_filter(df, f"KDJ金叉{tag}", BULL, need_volume=True))
    elif cross == "dead":
        tag = "（超买区）" if d > KDJ_DEAD_ZONE else ""
        items.append(_apply_exec_filter(df, f"KDJ死叉{tag}，减仓/回避" + _bear_volume_note(df), BEAR))
    else:
        rel = ">" if k > d else "<"
        items.append((f"K{rel}D，延续中(K={k:.1f} D={d:.1f})", NEUTRAL))

    if j > 100 or (k > KDJ_OVERBOUGHT and d > KDJ_OVERBOUGHT):
        items.append((f"高位超买(J={j:.1f} K={k:.1f} D={d:.1f})，减仓/回避", BEAR))
    elif j < 0 or (k < KDJ_OVERSOLD and d < KDJ_OVERSOLD):
        items.append((f"低位超卖(J={j:.1f} K={k:.1f} D={d:.1f})，下一交易日关注反弹", BULL))

    return {"name": name, "items": items}


def rsi_signals(df: pd.DataFrame) -> dict:
    name = "相对强弱 RSI"
    if not _last_finite(df, ["rsi6", "rsi12", "rsi24"], 1):
        return _insufficient(name)
    last = df.iloc[-1]
    r6, r24 = last["rsi6"], last["rsi24"]
    items = []
    if r6 > RSI_OVERBOUGHT:
        items.append((f"RSI6={r6:.1f}，严重超买，减仓/回避", BEAR))
    elif r6 < RSI_OVERSOLD:
        items.append((f"RSI6={r6:.1f}，严重超卖，下一交易日关注", BULL))
    elif r6 >= 50 >= r24:
        items.append((f"短强长弱(RSI6={r6:.1f}/RSI24={r24:.1f})，反弹结构", NEUTRAL))
    elif r6 <= 50 <= r24:
        items.append((f"短弱长强(RSI6={r6:.1f}/RSI24={r24:.1f})，回调结构", NEUTRAL))
    else:
        strength = "偏强" if r6 > 50 else "偏弱"
        items.append((f"RSI6={r6:.1f} RSI24={r24:.1f}，多空{strength}", NEUTRAL))

    if r6 > last["rsi12"] > r24:
        items.append(("RSI多头排序 6>12>24", BULL))
    elif r6 < last["rsi12"] < r24:
        items.append(("RSI空头排序 6<12<24，减仓/回避", BEAR))

    return {"name": name, "items": items}


def boll_signals(df: pd.DataFrame) -> dict:
    name = "布林通道 BOLL"
    need = ["close", "boll_up", "boll_low", "boll_mid"]
    if not _last_finite(df, need, 2):
        return _insufficient(name)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    close, up = last["close"], last["boll_up"]
    low, mid = last["boll_low"], last["boll_mid"]
    items = []
    if prev["close"] <= prev["boll_up"] < close:
        items.append(_apply_exec_filter(df, "突破布林上轨，动能强劲", BULL, need_volume=True))
    elif prev["close"] >= prev["boll_low"] > close:
        items.append(_apply_exec_filter(df, "跌破布林下轨，减仓/回避，超跌待修复", BEAR))
    elif close > up:
        items.append(("沿上轨运行，强势但防回轨", NEUTRAL))
    elif close < low:
        items.append(("沿下轨运行，弱势防阴跌，减仓/回避", NEUTRAL))
    else:
        pos_pct = (close - low) / max(up - low, 1e-9)
        zone = "上沿" if pos_pct > 0.66 else ("下沿" if pos_pct < 0.33 else "中轨附近")
        items.append((f"带内{zone}运行(位置{pos_pct * 100:.0f}%)", NEUTRAL))

    if len(df) >= BOLL_SLOPE_WINDOW and _finite(mid, df["boll_mid"].iloc[-BOLL_SLOPE_WINDOW]):
        slope = mid - df["boll_mid"].iloc[-BOLL_SLOPE_WINDOW]
        if slope > 0:
            items.append(("布林中轨向上，趋势支撑有效", BULL))
        elif slope < 0:
            items.append(("布林中轨向下，反弹承压，减仓/回避", BEAR))

    return {"name": name, "items": items}


def collect_signals(df: pd.DataFrame) -> list:
    return [ma_signals(df), macd_signals(df), kdj_signals(df), rsi_signals(df), boll_signals(df)]


def recent_events(df: pd.DataFrame, lookback: int = 15) -> pd.DataFrame:
    rows = []
    needed = ["ma5", "ma20", "dif", "dea", "kdj_k", "kdj_d", "date"]
    if any(c not in df.columns for c in needed) or len(df) < 2:
        return pd.DataFrame(rows)
    pairs = [
        ("MA5/MA20", df["ma5"], df["ma20"]),
        ("MACD(DIF/DEA)", df["dif"], df["dea"]),
        ("KDJ(K/D)", df["kdj_k"], df["kdj_d"]),
    ]
    for label, fast, slow in pairs:
        for dt, kind in _cross_events(fast, slow, df["date"], lookback):
            rows.append({
                "日期": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                "指标": label,
                "信号": "金叉 ▲ 偏多" if kind == "golden" else "死叉 ▼ 减仓/回避",
            })
    events = pd.DataFrame(rows)
    if not events.empty:
        events = events.sort_values("日期", ascending=False).reset_index(drop=True)
    return events


def score(groups: list) -> dict:
    """每组只投一票：组内偏多条数多于偏空则记多方，相反记空方，相等则中性。

    有效投票组数不足 SCORE_MIN_VOTES 时一律观望，避免 1:0 这类
    低样本比值给出误导性倾向。
    """
    bull = bear = 0
    for g in groups:
        b = sum(1 for _, s in g["items"] if s == BULL)
        e = sum(1 for _, s in g["items"] if s == BEAR)
        if b > e:
            bull += 1
        elif e > b:
            bear += 1
    total = bull + bear
    ratio = bull / total if total else 0.5
    if total < SCORE_MIN_VOTES:
        verdict, tone = "有效信号组不足，观望为宜", NEUTRAL
    elif ratio >= SCORE_STRONG:
        verdict, tone = "多方明显占优", BULL
    elif ratio >= SCORE_WEAK:
        verdict, tone = "多方略占优势", BULL
    elif ratio <= 1.0 - SCORE_STRONG:
        verdict, tone = "空方明显占优（减仓/回避）", BEAR
    elif ratio <= 1.0 - SCORE_WEAK:
        verdict, tone = "空方略占上风（减仓/回避）", BEAR
    else:
        verdict, tone = "多空均衡，观望为宜", NEUTRAL
    return {"bull": bull, "bear": bear, "total": total,
            "ratio": ratio, "verdict": verdict, "tone": tone}
