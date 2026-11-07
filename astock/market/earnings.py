"""财报研究数据包：保留累计口径，严格由相邻累计期计算单季度。

三表来源为新浪财务报表（金额：元），分部来源为东财主营构成。
每个指标按报告期对齐；缺上期、零/负比较基数均不补造增速。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
import re

import pandas as pd

from astock.market.fundamentals import _to_num, _fmt_amt

FLOW_FIELDS = {
    "revenue": ("营业收入", "营业总收入", "TOTAL_OPERATE_INCOME", "OPERATE_INCOME"),
    "cost": ("营业成本", "OPERATE_COST"),
    "net_profit": ("净利润", "NETPROFIT"),
    "parent_profit": ("归属于母公司所有者的净利润", "归属于母公司股东的净利润", "PARENT_NETPROFIT"),
    "deduct_profit": ("扣除非经常性损益后的净利润", "扣非净利润", "DEDUCT_PARENT_NETPROFIT"),
    "selling_expense": ("销售费用", "SELL_EXPENSE"),
    "admin_expense": ("管理费用", "MANAGE_EXPENSE"),
    "rd_expense": ("研发费用", "RESEARCH_EXPENSE"),
    "finance_expense": ("财务费用", "FINANCE_EXPENSE"),
    "ocf": ("经营活动产生的现金流量净额", "NETCASH_OPERATE"),
}
STOCK_FIELDS = {
    "receivables": ("应收账款", "ACCOUNTS_RECE"),
    "inventory": ("存货", "INVENTORY"),
    "goodwill": ("商誉", "GOODWILL"),
}
LABELS = {"revenue": "营收", "cost": "营业成本", "net_profit": "合并净利润",
          "parent_profit": "归母净利润", "deduct_profit": "扣非净利润",
          "abstract_profit": "摘要净利润", "ocf": "经营现金流", "receivables": "应收账款",
          "inventory": "存货", "goodwill": "商誉", "selling_expense": "销售费用",
          "admin_expense": "管理费用", "rd_expense": "研发费用", "finance_expense": "财务费用"}


def fetch_earnings_data(code6: str, ak_mod=None) -> dict:
    """并发有界取三表与分部，不把一个接口失败伪装为公司未披露。"""
    if ak_mod is None:
        import akshare as ak_mod
    from astock.market.data_fetcher import normalize_symbol, default_request_timeout
    stock = normalize_symbol(code6)
    jobs = {"income": lambda: ak_mod.stock_financial_report_sina(stock=stock, symbol="利润表"),
            "balance": lambda: ak_mod.stock_financial_report_sina(stock=stock, symbol="资产负债表"),
            "cashflow": lambda: ak_mod.stock_financial_report_sina(stock=stock, symbol="现金流量表"),
            "segments": lambda: ak_mod.stock_zygc_em(symbol=stock.upper())}
    out = {"errors": {}}
    pool = ThreadPoolExecutor(max_workers=4)
    with default_request_timeout(10):
        futures = {key: pool.submit(fn) for key, fn in jobs.items()}
        done, _ = wait(futures.values(), timeout=20)
        for key, future in futures.items():
            try:
                if future not in done:
                    raise TimeoutError()
                value = future.result()
                out[key] = value if isinstance(value, pd.DataFrame) else pd.DataFrame()
                if out[key].empty:
                    out["errors"][key] = "empty"
            except Exception as exc:
                out[key] = pd.DataFrame()
                out["errors"][key] = type(exc).__name__
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def _date(value):
    raw = str(value).strip()
    if re.fullmatch(r"\d{8}(?:\.0)?", raw):
        raw = raw[:8]
    return pd.to_datetime(raw, errors="coerce")


def _records(frame, mapping):
    if frame is None or frame.empty:
        return {}
    date_col = next((c for c in ("报告日", "报告期", "报告日期", "REPORT_DATE", "日期", "_date")
                     if c in frame.columns), None)
    if date_col is None:
        return {}
    out = {}
    for _, row in frame.iterrows():
        if "类型" in frame and pd.notna(row["类型"]) and "合并" not in str(row["类型"]):
            continue
        dt = _date(row[date_col])
        if pd.isna(dt) or (dt.month, dt.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
            continue
        values = {}
        for key, aliases in mapping.items():
            for col in aliases:
                if col in row:
                    num = _to_num(row[col])
                    if num is not None:
                        values[key] = num
                        break
        if values:
            out[dt.date().isoformat()] = values
    return out


def _growth(current, previous):
    # 亏损转盈不能用普通增长率表达，保留原值供分析。
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1) * 100


def build_earnings_packet(abstract_df: pd.DataFrame, data: dict | None = None) -> dict:
    data = data or {}
    periods = {}
    for kind in ("income", "cashflow", "balance"):
        for dt, values in _records(data.get(kind), {**FLOW_FIELDS, **STOCK_FIELDS}).items():
            periods.setdefault(dt, {}).update(values)
    # 摘要净利润不擅自重命名为归母/合并净利润；各期独立保留来源。
    abstract_map = {"revenue": ("营业总收入", "营业总收入(元)", "营业收入"),
                    "abstract_profit": ("净利润",),
                    "deduct_profit": ("扣非净利润", "扣除非经常性损益后的净利润")}
    for dt, values in _records(abstract_df, abstract_map).items():
        target = periods.setdefault(dt, {})
        for key, value in values.items():
            target.setdefault(key, value)
    quarters = {}
    flow_keys = set(FLOW_FIELDS) | {"abstract_profit"}
    for dt, row in sorted(periods.items()):
        timestamp = pd.Timestamp(dt)
        previous = (timestamp - pd.offsets.QuarterEnd()).date().isoformat()
        values = {}
        for key in flow_keys:
            if key not in row:
                continue
            if timestamp.month == 3:
                values[key] = row[key]
            elif key in periods.get(previous, {}):
                values[key] = row[key] - periods[previous][key]
        if values:
            quarters[dt] = values
    for dt, row in quarters.items():
        timestamp = pd.Timestamp(dt)
        year_ago = (timestamp - pd.DateOffset(years=1)).date().isoformat()
        previous = (timestamp - pd.offsets.QuarterEnd()).date().isoformat()
        row["yoy"] = {key: _growth(row.get(key), quarters.get(year_ago, {}).get(key)) for key in flow_keys}
        row["qoq"] = {key: _growth(row.get(key), quarters.get(previous, {}).get(key)) for key in flow_keys}
        revenue, cost = row.get("revenue"), row.get("cost")
        if revenue and revenue > 0:
            row["gross_margin"] = (revenue - cost) / revenue * 100 if cost is not None else None
            row["net_margin"] = row.get("net_profit", 0) / revenue * 100 if "net_profit" in row else None
            expenses = [row.get(key) for key in ('selling_expense', 'admin_expense', 'rd_expense', 'finance_expense')]
            if all(v is not None for v in expenses):
                row['expense_ratio'] = sum(expenses) / revenue * 100
    segments = []
    frame = data.get("segments")
    if isinstance(frame, pd.DataFrame) and not frame.empty and "报告日期" in frame:
        dates = frame["报告日期"].map(_date)
        cutoff = pd.Timestamp(max(periods)) if periods else pd.Timestamp.now()
        available = dates[dates <= cutoff]
        if not available.empty:
            date = available.max()
            for _, row in frame[dates == date].head(18).iterrows():
                segments.append({"period": date.date().isoformat(), "category": str(row.get("分类类型", "")),
                                 "name": str(row.get("主营构成", "")), "revenue": _to_num(row.get("主营收入")),
                                 "cost": _to_num(row.get("主营成本"))})
    return {"period": max(periods) if periods else "", "cumulative": periods,
            "quarters": quarters, "segments": segments, "fetch_errors": data.get("errors", {})}


def earnings_text(packet: dict) -> str:
    if not packet.get("period"):
        return "【财报拆解】三表与历史序列未获取，不能计算单季度业绩。"
    lines = ["【财报历史与单季度拆解】金额单位：元；三表为新浪合并报表，摘要净利润为同花顺原始口径。",
             "累计为年初至报告期末；单季=本期累计−同年上一季度累计，Q1直接取累计；缺相邻期不计算。"]
    for dt in sorted(packet["cumulative"])[-8:]:
        row = packet["cumulative"][dt]
        lines.append(dt + " 累计：" + "；".join(f"{LABELS[k]}={_fmt_amt(v)}" for k, v in row.items()))
    for dt in sorted(packet["quarters"])[-6:]:
        row = packet["quarters"][dt]
        parts = []
        for key in ("revenue", "parent_profit", "net_profit", "abstract_profit", "deduct_profit", "ocf"):
            if key not in row:
                continue
            change = []
            for field, label in (("yoy", "同比"), ("qoq", "环比")):
                value = row[field].get(key)
                change.append(f"{label}{value:+.2f}%" if value is not None else f"{label}不可计算（缺比较期或基数≤0）")
            parts.append(f"{LABELS[key]}={_fmt_amt(row[key])}（{'，'.join(change)}）")
        if row.get("gross_margin") is not None:
            parts.append(f"单季毛利率={row['gross_margin']:.2f}%")
            year_ago = (pd.Timestamp(dt) - pd.DateOffset(years=1)).date().isoformat()
            previous_margin = packet['quarters'].get(year_ago, {}).get('gross_margin')
            if previous_margin is not None:
                parts.append(f"毛利率同比变化={row['gross_margin']-previous_margin:+.2f}个百分点")
        if row.get('expense_ratio') is not None:
            parts.append(f"销售/管理/研发/财务四费率合计={row['expense_ratio']:.2f}%")
        if row.get("net_profit", 0) > 0 and "ocf" in row:
            parts.append(f"经营现金流/合并净利润={row['ocf']/row['net_profit']:.2f}倍")
        lines.append(dt + " 单季：" + "；".join(parts))
    dt = packet['period']
    current = packet['cumulative'][dt]
    prior = packet['cumulative'].get((pd.Timestamp(dt) - pd.DateOffset(years=1)).date().isoformat(), {})
    quality = []
    for key in ('revenue', 'receivables', 'inventory'):
        growth = _growth(current.get(key), prior.get(key))
        if growth is not None:
            quality.append(f"{LABELS[key]}同比={growth:+.2f}%")
    if current.get('net_profit', 0) > 0 and 'ocf' in current:
        quality.append(f"累计经营现金流/合并净利润={current['ocf']/current['net_profit']:.2f}倍")
    if quality:
        lines.append(f"盈利质量比较（{dt}，同报告期）：" + '；'.join(quality))
    for row in packet.get("segments", []):
        revenue, cost = row["revenue"], row["cost"]
        margin = f"，毛利率={(revenue-cost)/revenue*100:.2f}%" if revenue and revenue > 0 and cost is not None else ""
        lines.append(f"分部（{row['period']}，{row['category']}）{row['name']}：收入={_fmt_amt(revenue)}{margin}")
    if packet.get("fetch_errors"):
        lines.append("接口未获取：" + "、".join(packet["fetch_errors"]) + "；接口失败不能表述为公司未披露。")
    lines.append("研究缺口：并购贡献/同口径内生增长、一致预期与预告比较需读取公告或独立预测资料；不能由合并增速反推。"
                 "各分部以各自报告期为准，不外推未披露的季度分部业绩；多个分类不可相加。")
    return "\n".join(lines)
