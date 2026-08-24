import pandas as pd

from signals import BULL, BEAR, collect_signals, flag_value, recent_events, score
from trade_plan import build_trade_plan

_TONE_MARK = {BULL: "▲ 看多", BEAR: "▼ 减仓/回避", None: "— 中性"}
_TONE_COLOR = {BULL: "#e74c3c", BEAR: "#27ae60", None: "#7f8c8d"}


def build_report(df: pd.DataFrame, name: str = "", symbol: str = "",
                 period: str = "daily") -> dict:
    groups = collect_signals(df)
    s = score(groups)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    chg = (last["close"] / prev["close"] - 1) * 100
    prev_label = "较前一交易日" if period == "daily" else "较上一周"
    lines = [
        f"【{name}({symbol}) 技术面综述】",
        f"最新收盘 {last['close']:.2f} 元，{prev_label}{'上涨' if chg >= 0 else '下跌'} {abs(chg):.2f}%。",
        f"综合评估：{_TONE_MARK.get(s['tone'], '—')}（{s['bull']}组偏多 / {s['bear']}组偏空）——{s['verdict']}。",
        "操作提示：A股T+1，信号按已收盘K线计，最早下一交易日开盘关注；空头仅指减仓/回避，不假设可做空。",
        "",
    ]
    for g in groups:
        lines.append(f"◆ {g['name']}")
        for text, status in g["items"]:
            mark = "▲" if status == BULL else ("▼" if status == BEAR else "—")
            lines.append(f"   [{mark}] {text}")
        lines.append("")

    vol_bits = []
    turnover = last["turnover"] if "turnover" in df.columns else None
    if turnover is not None and not pd.isna(turnover):
        # data_fetcher.ensure_turnover_percent 已把换手统一为百分数
        t_pct = float(turnover)
        level = "活跃" if t_pct > 5 else ("温和" if t_pct > 1.5 else "低迷")
        vol_bits.append(f"换手率 {t_pct:.2f}%（{level}）")
    if "vol_ratio" in df.columns and not pd.isna(last.get("vol_ratio")):
        r = float(last["vol_ratio"])
        level = "放量" if r >= 1.2 else ("平量" if r >= 0.8 else "缩量")
        vol_bits.append(f"量比 VOL/MA5={r:.2f}（{level}）")
    if vol_bits:
        lines.append("◆ 量能参考：" + "；".join(vol_bits) + "。")

    if flag_value(last, "is_limit_up"):
        lines.append("◆ 涨跌停：最新K线收于涨停，买入信号当日不可成交。")
    elif flag_value(last, "is_limit_down"):
        lines.append("◆ 涨跌停：最新K线收于跌停，卖出可能无法成交，按减仓/回避处理。")

    plan = build_trade_plan(df, s["tone"])
    if plan:
        lines.append("")
        lines.append("◆ 价位参考（近端支撑压力 + ATR 缓冲推算）：")
        for ln in plan["lines"]:
            lines.append(f"   {ln}")
        lines.append(f"   {plan['note']}")

    events = recent_events(df)
    if not events.empty:
        lines.append("")
        lines.append(f"◆ 近期交叉信号（近15个周期，共{len(events)}个）：")
        for _, row in events.head(8).iterrows():
            lines.append(f"   {row['日期']}  {row['指标']}  {row['信号']}")
    else:
        lines.append("")
        lines.append("◆ 近15个周期内无均线/MACD/KDJ交叉信号。")

    return {
        "groups": groups,
        "score": s,
        "text": "\n".join(lines),
        "events": events,
        "plan": plan,
    }


def tone_color(tone: str) -> str:
    return _TONE_COLOR.get(tone, _TONE_COLOR[None])
