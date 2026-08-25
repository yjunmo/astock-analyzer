"""把已计算好的技术面数据组装为 LLM 可读的紧凑上下文。

技能文件为带 frontmatter 的 Markdown：
---
name: 技能名
temperature: 0.3
max_tokens: 1600
---
正文可使用占位符 {report} {plan} {bars} {snapshot}，由 render_prompt 注入。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

BARS_TAIL = 90
_BARS_COLUMNS = ("date", "open", "high", "low", "close", "pct_chg",
                 "vol_ratio", "ma5", "ma20", "dif", "dea", "kdj_k", "rsi6")

DISCLAIMER = (
    "\n\n---\n"
    "输出要求：明确区分「指标事实」与「概率推断」；A股 T+1、无个股做空工具，"
    "偏空结论一律表述为减仓/回避；推理阶段保持精炼——只保留对结论有实质影响的"
    "关键推导，避免冗长枚举；结尾必须提醒：以上为技术数据推演，不构成投资建议。"
)


def _fmt(v, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(f):
        return "-"
    return f"{f:.{nd}f}"


def bars_table(df: pd.DataFrame, tail: int = BARS_TAIL) -> str:
    """尾随K线压缩表：管道分隔，含涨跌幅与关键指标列。"""
    d = df.tail(tail).copy()
    d["pct_chg"] = (d["close"] / d["close"].shift(1) - 1) * 100
    cols = [c for c in _BARS_COLUMNS if c in d.columns]
    rows = ["|".join(cols)]
    for _, r in d.iterrows():
        vals = []
        for c in cols:
            if c == "date":
                vals.append(pd.Timestamp(r[c]).strftime("%m-%d"))
            else:
                vals.append(_fmt(r[c], nd=1 if c == "pct_chg" else 2))
        rows.append("|".join(vals))
    return "\n".join(rows)


def plan_lines(report_result: dict) -> str:
    plan = report_result.get("plan")
    if not plan:
        return "无"
    out = [f"- {label}：{value}" for label, value in plan.get("cards", [])]
    if plan.get("note"):
        out.append(f"- 说明：{plan['note']}")
    return "\n".join(out)


def snapshot_text(snapshot: Optional[dict]) -> str:
    if not snapshot or not snapshot.get("price"):
        return "无实时快照"
    chg = ""
    if snapshot.get("prev_close"):
        chg = f"（{snapshot['price'] / snapshot['prev_close'] - 1:+.2%}）"

    def px(k):
        v = snapshot.get(k)
        return f"{float(v):.2f}" if isinstance(v, (int, float)) else "-"

    return (f"{snapshot.get('time', '-')} 最新价{px('price')}{chg}"
            f" 开{px('open')} 高{px('high')} 低{px('low')} 昨收{px('prev_close')}")


def build_placeholders(df: pd.DataFrame, report_result: dict,
                       snapshot: Optional[dict] = None,
                       bars_tail: Optional[int] = None,
                       market_ctx: str = "") -> dict:
    """组装占位符。bars_tail 可由技能 frontmatter 覆盖（省 token 用）。"""
    return {
        "report": report_result.get("text", ""),
        "plan": plan_lines(report_result),
        "bars": bars_table(df, tail=bars_tail or BARS_TAIL),
        "snapshot": snapshot_text(snapshot),
        "market_ctx": market_ctx or "（未采集市场环境数据）",
    }


def render_prompt(skill_md: str, placeholders: dict) -> str:
    """替换占位符并追加统一免责约束；未知占位符原样保留。"""
    out = skill_md
    for k, v in placeholders.items():
        out = out.replace("{" + k + "}", v)
    return out.rstrip() + DISCLAIMER


def parse_frontmatter(md_text: str) -> tuple:
    """解析 --- 包裹的简单 key: value 头部；返回 (meta, body)。"""
    meta: dict = {}
    body = md_text
    stripped = md_text.lstrip()
    if stripped.startswith("---"):
        lines = stripped.splitlines()
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            for ln in lines[1:end]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip("'\"")
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body
