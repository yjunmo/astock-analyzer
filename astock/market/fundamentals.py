"""基本面数据模块：财务指标趋势 + 同花顺财务摘要 + 估值快照。

数据来源（均防御式解析，任一接口失败降级为"数据缺失"标注，不致命）：
- 新浪财务指标 `stock_financial_analysis_indicator`：季度序列，供趋势图与盈利能力快照
- 同花顺财务摘要 `stock_financial_abstract_ths`：按报告期的营收/净利/增速/现金流等
- 腾讯行情、东财全市场快照：PE/PB/总市值等估值字段

所有网络函数接受 ak_mod 注入便于离线测试；纯解析函数不依赖网络。
输出为结构化 dict 与紧凑文本（{fundamentals} 占位符），每项标注报告期。
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------- 通用解析

_WAN = 1e4
_YI = 1e8


def _to_num(v) -> Optional[float]:
    """'1.47亿' / '12.5%' / 'False' / '--' / '' → float 或 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = str(v).strip().replace(",", "")
    if not s or s in ("False", "None", "--", "-", "nan"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        s, mult = s[:-1], _YI
    elif s.endswith("万"):
        s, mult = s[:-1], _WAN
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        f = float(s)
    except ValueError:
        return None
    if not math.isfinite(f):
        return None
    return f * mult


def _fmt_amt(v) -> str:
    f = _to_num(v)
    if f is None:
        return "--"
    if abs(f) >= _YI:
        return f"{f / _YI:.2f} 亿"
    if abs(f) >= _WAN:
        return f"{f / _WAN:.2f} 万"
    return f"{f:.0f}"


def _col(df: pd.DataFrame, *candidates) -> Optional[str]:
    """返回第一个存在的列名；全部缺失返回 None（防御 akshare 字段变动）。"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _cell(df: pd.DataFrame, row_idx: int, *candidates) -> Optional[float]:
    c = _col(df, *candidates)
    if c is None:
        return None
    try:
        return _to_num(df[c].iloc[row_idx])
    except (IndexError, KeyError):
        return None


# ---------------------------------------------------------------- 网络抓取

_TENCENT_FIELDS = {
    # 腾讯 qt.gtimg.cn 字段索引 → 展示名；市值/成交额单位为亿/万，解析时换算
    "名称": 1, "最新价": 3, "昨收": 4, "今开": 5, "最高": 33, "最低": 34,
    "成交量": 36, "成交额万": 37, "换手率": 38, "市盈率": 39,
    "流通市值亿": 44, "总市值亿": 45, "市净率": 46, "时间": 30,
}

_TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0",
                    "Referer": "https://gu.qq.com/"}


def _fetch_spot_tencent(code6: str, http_get=None) -> dict:
    """腾讯行情兜底：东财 push2 整体故障时仍可拿到 PE/PB/市值/换手率。"""
    from astock.market import data_fetcher as dtf
    try:
        symbol = dtf.normalize_symbol(code6)
    except ValueError:
        return {}
    get = http_get or _default_get
    try:
        r = get(f"https://qt.gtimg.cn/q={symbol}",
                timeout=8, headers=_TENCENT_HEADERS)
        payload = r.content.decode("gbk", errors="ignore").split('"')[1]
        fields = payload.split("~")
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for key, idx in _TENCENT_FIELDS.items():
        if idx >= len(fields):
            continue
        raw = fields[idx].strip()
        if key.endswith("亿"):
            f = _to_num(raw)
            out[key[:-1]] = f"{f * _YI:.0f}" if f is not None else raw
        elif key.endswith("万"):
            f = _to_num(raw)
            out[key[:-1]] = f"{f * _WAN:.0f}" if f is not None else raw
        else:
            out[key] = raw
    return out or {}


def _default_get(url: str, timeout: int = 8, headers: dict = None):
    import requests
    return requests.get(url, timeout=timeout, headers=headers)


def has_valuation(values: dict) -> bool:
    return (all(_pick_val(values, field) is not None for field in ("市盈率", "市净率"))
            and (_pick_val(values, "总市值") or 0) > 0)


def fetch_spot_extra(code6: str, ak_mod=None, http_get=None) -> dict:
    """东财估值快照（市盈率/市净率/总市值/换手率等）。

    按字段补齐：盘口只提供行情，腾讯和全市场快照补估值。
    有行情不等于有估值，部分字段成功也必须继续兜底。
    """
    code6 = str(code6).zfill(6)

    def _ak():
        if ak_mod is not None:
            return ak_mod
        import akshare
        return akshare

    out = {}
    def merge(values):
        for key, value in values.items():
            if key not in out or _to_num(out[key]) is None:
                if _to_num(value) is not None or key in {"名称", "时间"}:
                    out[key] = value

    def complete():
        return has_valuation(out)

    try:
        ak = _ak()
        df = ak.stock_bid_ask_em(symbol=code6)
        merge(dict(zip(df["item"].astype(str), df["value"])))
        if complete():
            return out
    except Exception:  # noqa: BLE001 —— 估值快照缺失不致命，走兜底
        pass
    tx = _fetch_spot_tencent(code6, http_get=http_get)
    merge(tx)
    if complete():
        return out
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot_em()
        hit = df[df["代码"].astype(str).str.zfill(6) == code6]
        if not hit.empty:
            merge({str(k): v for k, v in hit.iloc[0].to_dict().items()})
    except Exception:  # noqa: BLE001
        pass
    return out


def fetch_financial_indicator(code6: str, start_year: str = "2019",
                              ak_mod=None) -> pd.DataFrame:
    """新浪财务指标季度序列。失败返回空 DataFrame。"""
    try:
        ak = ak_mod
        if ak is None:
            import akshare as ak
        df = ak.stock_financial_analysis_indicator(symbol=code6,
                                                   start_year=start_year)
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.copy()
        date_col = _col(out, "日期", "date")
        if date_col:
            out = out.assign(_date=pd.to_datetime(out[date_col], errors="coerce"))
            return out.sort_values("_date").reset_index(drop=True)
        return out.reset_index(drop=True)  # 无日期列时按原顺序保留数据
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def fetch_financial_abstract(code6: str, ak_mod=None) -> pd.DataFrame:
    """同花顺财务摘要（按报告期）。失败返回空 DataFrame。"""
    try:
        ak = ak_mod
        if ak is None:
            import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=code6, indicator="按报告期")
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.copy()
        p_col = _col(out, "报告期", "日期", "date")
        if p_col:
            out = out.assign(_date=pd.to_datetime(out[p_col], errors="coerce"))
            return out.sort_values("_date").reset_index(drop=True)
        return out.reset_index(drop=True)  # 无日期列时按原顺序保留数据
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


# ---------------------------------------------------------------- 快照组装

_INDICATOR_COLS = {
    "roe": ("净资产收益率(%)", "净资产收益率"),
    "gross_margin": ("销售毛利率(%)", "毛利率"),
    "net_margin": ("销售净利率(%)", "净利率"),
    "debt_ratio": ("资产负债率(%)", "资产负债率"),
    "ocf_per_share": ("每股经营现金流量(元)", "每股经营现金流"),
    "current_ratio": ("流动比率",),
    "quick_ratio": ("速动比率",),
    "eps": ("摊薄每股收益(元)", "基本每股收益"),
}

_ABSTRACT_COLS = {
    "revenue": ("营业总收入", "营业总收入(元)"),
    "revenue_yoy": ("营业总收入同比增长率", "营业收入同比增长率"),
    "profit": ("净利润",),
    "profit_yoy": ("净利润同比增长率",),
    "deduct_profit_yoy": ("扣非净利润同比增长率",),
    "eps": ("基本每股收益", "摊薄每股收益(元)"),
    "ocf_per_share": ("每股经营现金流", "每股经营现金流量(元)"),
    "gross_margin": ("销售毛利率", "销售毛利率(%)"),
    "net_margin": ("销售净利率", "销售净利率(%)"),
    "roe": ("净资产收益率", "净资产收益率(%)"),
    "debt_ratio": ("资产负债率", "资产负债率(%)"),
    "current_ratio": ("流动比率",),
    "quick_ratio": ("速动比率",),
}


def _latest_cells(df: pd.DataFrame, col_map: dict) -> dict:
    """取 DataFrame 最新一行的映射值。"""
    out = {}
    for key, cands in col_map.items():
        out[key] = _cell(df, -1, *cands)
    return out


def _pick_val(d: dict, *keywords) -> Optional[float]:
    for k, v in d.items():
        if any(w in str(k) for w in keywords):
            f = _to_num(v)
            if f is not None:
                return f
    return None


def _ensure_date_col(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    if "_date" not in out:
        c = _col(out, "日期", "报告期", "date")
        out["_date"] = (pd.to_datetime(out[c], errors="coerce") if c else pd.NaT)
    return out.sort_values("_date", na_position="first").reset_index(drop=True)


def build_fundamentals(indicator_df: pd.DataFrame,
                       abstract_df: pd.DataFrame,
                       extras: Optional[dict] = None,
                       earnings_data: Optional[dict] = None) -> dict:
    """组装结构化基本面快照。

    优先级：摘要表（报告期口径）> 指标序列 > 估值快照；缺失维度记入 gaps。
    """
    extras = extras or {}
    indicator_df = _ensure_date_col(indicator_df)
    abstract_df = _ensure_date_col(abstract_df)
    gaps: list = []
    # 指标源较旧时不得冒充摘要报告期的指标。
    if len(indicator_df) and len(abstract_df):
        latest = abstract_df["_date"].iloc[-1]
        aligned = indicator_df[indicator_df["_date"] == latest]
    else:
        aligned = indicator_df
    ind = _latest_cells(aligned, _INDICATOR_COLS) if len(aligned) else {}
    abs_ = _latest_cells(abstract_df, _ABSTRACT_COLS) if len(abstract_df) else {}

    def merged(key: str):
        v = abs_.get(key)
        if v is None:
            v = ind.get(key)
        return v

    period = "未知报告期"
    if len(abstract_df) and "_date" in abstract_df.columns:
        p = abstract_df["_date"].iloc[-1]
        if pd.notna(p):
            period = pd.Timestamp(p).strftime("%Y-%m-%d")

    roe, gross, net_m = merged("roe"), merged("gross_margin"), merged("net_margin")
    if roe is None and gross is None and net_m is None:
        gaps.append("盈利能力（ROE/毛利率/净利率）")

    revenue, revenue_yoy = merged("revenue"), merged("revenue_yoy")
    profit, profit_yoy = merged("profit"), merged("profit_yoy")
    if revenue_yoy is None and profit_yoy is None:
        gaps.append("成长性（营收/净利同比增速）")

    debt_ratio, cur, quick = (merged("debt_ratio"), merged("current_ratio"),
                              merged("quick_ratio"))
    if debt_ratio is None and cur is None and quick is None:
        gaps.append("偿债能力（资产负债率/流动速动比率）")

    ocf = merged("ocf_per_share")
    if ocf is None:
        gaps.append("现金流（每股经营现金流）")

    pe = _pick_val(extras, "市盈率")
    pb = _pick_val(extras, "市净率")
    mcap = _pick_val(extras, "总市值")
    if pe is None or pb is None or mcap is None:
        gaps.append("估值（PE/PB/总市值）")

    trend = {}
    if len(indicator_df):
        trend["periods"] = [
            pd.Timestamp(t).strftime("%Y-%m-%d") if pd.notna(t) else "-"
            for t in indicator_df["_date"]
        ]
        for key in ("roe", "gross_margin", "net_margin", "debt_ratio"):
            c = _col(indicator_df, *_INDICATOR_COLS[key])
            if c:
                trend[key] = [v if pd.notna(v) else None
                              for v in indicator_df[c].map(_to_num)]
    if len(abstract_df):
        trend["report_periods"] = [
            pd.Timestamp(t).strftime("%Y-%m-%d") if pd.notna(t) else "-"
            for t in abstract_df["_date"]
        ]
        for key in ("revenue", "profit"):
            c = _col(abstract_df, *_ABSTRACT_COLS[key])
            if c:
                trend[key] = [v if pd.notna(v) else None
                              for v in abstract_df[c].map(_to_num)]

    from astock.market.earnings import build_earnings_packet
    earnings = build_earnings_packet(abstract_df, earnings_data)
    if period == "未知报告期":
        if len(indicator_df) and pd.notna(indicator_df['_date'].iloc[-1]):
            period = indicator_df['_date'].iloc[-1].date().isoformat()
        elif earnings['period']:
            period = earnings['period']
    return {
        "ok": bool(len(indicator_df) or len(abstract_df) or extras or earnings['period']),
        "period": period,
        "gaps": gaps,
        "valuation": {"pe": pe, "pb": pb, "mcap": mcap},
        "profit": {"roe": roe, "gross_margin": gross, "net_margin": net_m,
                   "eps": merged("eps")},
        "growth": {"revenue": revenue, "revenue_yoy": revenue_yoy,
                   "profit": profit, "profit_yoy": profit_yoy,
                   "deduct_profit_yoy": merged("deduct_profit_yoy")},
        "solvency": {"debt_ratio": debt_ratio, "current_ratio": cur,
                     "quick_ratio": quick},
        "cashflow": {"ocf_per_share": ocf},
        "trend": trend,
        "earnings": earnings,
    }


def fundamentals_text(snap: dict) -> str:
    """把基本面快照渲染为注入 AI 的紧凑文本。数据缺口如实声明。"""
    if not snap or not snap.get("ok"):
        return "（基本面数据缺失：财务指标与摘要接口均获取失败，无法判断，勿编造）"

    def pct(v):
        return "-" if v is None else f"{v:+.2f}%"

    def num(v):
        return "-" if v is None else f"{v:.2f}"

    v, pf, g, s, cf = (snap["valuation"], snap["profit"], snap["growth"],
                       snap["solvency"], snap["cashflow"])
    lines = [f"报告期：{snap['period']}（同花顺财务摘要口径）"]
    lines.append("【估值】PE(动)=" + num(v["pe"]) + " · PB=" + num(v["pb"])
                 + " · 总市值=" + _fmt_amt(v["mcap"]))
    lines.append("【盈利能力】ROE=" + num(pf["roe"]) + "% · 销售毛利率="
                 + num(pf["gross_margin"]) + "% · 销售净利率="
                 + num(pf["net_margin"]) + "% · 每股收益=" + num(pf["eps"]) + "元")
    lines.append("【成长性】营业总收入=" + _fmt_amt(g["revenue"])
                 + "（同比" + pct(g["revenue_yoy"]) + "）· 净利润="
                 + _fmt_amt(g["profit"]) + "（同比" + pct(g["profit_yoy"])
                 + "）· 扣非净利同比" + pct(g["deduct_profit_yoy"]))
    lines.append("【偿债】资产负债率=" + num(s["debt_ratio"]) + "% · 流动比率="
                 + num(s["current_ratio"]) + " · 速动比率=" + num(s["quick_ratio"]))
    lines.append("【现金流】每股经营现金流=" + num(cf["ocf_per_share"]) + "元")
    if snap.get("gaps"):
        lines.append("数据缺口：" + "、".join(snap["gaps"]) + "（缺失维度如实声明，不得编造）")
    from astock.market.earnings import earnings_text
    lines.append(earnings_text(snap.get("earnings") or {}))
    return "\n".join(lines)


def trend_series(snap: dict, key: str):
    """从 trend 块取 (periods, values) 序列；缺列返回 ([], [])。"""
    t = (snap or {}).get("trend") or {}
    return t.get("periods", []), t.get(key, [])


def revenue_profit_series(snap: dict):
    """营收/净利柱状图序列（按报告期）。"""
    t = (snap or {}).get("trend") or {}
    return (t.get("report_periods", []), t.get("revenue", []),
            t.get("profit", []))
