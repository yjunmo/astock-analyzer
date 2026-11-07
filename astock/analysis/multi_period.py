"""多周期对比：日/周/月三周期信号共振。

输入为已计算指标与涨跌停标志的日线 DataFrame（get_history + compute_all 之后），
周/月线由日线本地派生，不发起额外网络请求；各周期独立容错，
样本不足的周期标记 ok=False 而非报错。
"""
from __future__ import annotations

from typing import Optional

from astock.market import data_fetcher as dtf
from astock.analysis.indicators import compute_all
from astock.analysis.report import build_report
from astock.analysis.signals import BEAR, BULL

PERIODS = ("daily", "weekly", "monthly")
LABELS = {"daily": "日线", "weekly": "周线", "monthly": "月线"}
MIN_PERIOD_BARS = 20   # 参与共振的最少周期数（MA20 起步）


def _one_period(df_daily, period: str, symbol: str = "",
                name: str = "") -> dict:
    if period != "daily":
        resampler = {"weekly": getattr(dtf, "to_weekly", None),
                     "monthly": getattr(dtf, "to_monthly", None)}[period]
        if resampler is None:
            # 防御：运行中的 Streamlit 进程缓存了旧版 data_fetcher（缺少
            # to_weekly/to_monthly）时，不崩溃而是给出可执行提示。
            return {"period": period, "label": LABELS[period], "ok": False,
                    "reason": "模块版本过旧——请重启应用以加载最新代码"}
        df = resampler(df_daily)
        df = dtf.drop_unclosed_bar(df, period)
        df = compute_all(df, period=period)
    else:
        df = df_daily
    if df is None or len(df) < MIN_PERIOD_BARS:
        return {"period": period, "label": LABELS[period], "ok": False,
                "reason": f"样本不足（{0 if df is None else len(df)}根）"}
    report = build_report(df, name=name, symbol=symbol, period=period)
    s = report["score"]
    return {"period": period, "label": LABELS[period], "ok": True,
            "n_bars": len(df), "tone": s["tone"], "verdict": s["verdict"],
            "bull": s["bull"], "bear": s["bear"],
            "close": float(df["close"].iloc[-1]),
            "df": df, "report": report}


def build_multi_period(df_daily, symbol: str = "", name: str = "") -> dict:
    entries = {p: _one_period(df_daily, p, symbol=symbol, name=name)
               for p in PERIODS}
    ok = [e for e in entries.values() if e["ok"]]
    tones = [e["tone"] for e in ok]
    bulls = sum(1 for t in tones if t == BULL)
    bears = sum(1 for t in tones if t == BEAR)

    if not ok:
        verdict = "多周期数据不足，无法共振判断"
    elif bulls == len(tones):
        verdict = f"{len(tones)}周期共振偏多"
    elif bears == len(tones):
        verdict = f"{len(tones)}周期共振偏空（减仓/回避）"
    elif bulls and bears:
        verdict = "多周期分歧，以大周期为锚、谨慎应对"
    else:
        verdict = "多周期方向未明，观望为宜"
    return {
        "entries": entries,
        "verdict": verdict,
        "bulls": bulls,
        "bears": bears,
        "n_ok": len(ok),
    }


def multi_period_text(mp: Optional[dict]) -> str:
    """多周期共振结论的紧凑文本（供注入 AI 提示词）。"""
    if not mp or not mp.get("entries"):
        return "（多周期对比数据缺失）"
    lines = [f"多周期共振结论：{mp['verdict']}"]
    for p in PERIODS:
        e = mp["entries"].get(p)
        if not e:
            continue
        if not e["ok"]:
            lines.append(f"- {e['label']}：不可用（{e.get('reason', '数据不足')}）")
        else:
            mark = {"bullish": "▲", "bearish": "▼"}.get(e["tone"], "—")
            lines.append(f"- {e['label']}：{mark} {e['verdict']}"
                         f"（多 {e['bull']} : 空 {e['bear']}，{e['n_bars']}根）")
    lines.append("多周期结论由各周期已收盘K线独立计算后对比得出，仅供研究参考。")
    return "\n".join(lines)
