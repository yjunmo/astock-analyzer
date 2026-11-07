"""股票云图：有界行情采集、行业归类与可复用的纯计算。"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

CN_TZ = ZoneInfo("Asia/Shanghai")
API = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
NUMERIC = ("最新价", "涨跌幅", "成交额", "总市值", "流通市值", "换手率")
AREA_FIELDS = ("总市值", "流通市值", "成交额", "等权")
MARKETS = ("全部A股", "沪市主板", "深市主板", "创业板", "科创板", "北交所")


def _get(url, **kwargs):
    return requests.get(url, headers=HEADERS, timeout=8, **kwargs)


def _parallel(specs, fn, timeout=35):
    """限制整批等待时间；单个失败不影响其余页。"""
    pool = ThreadPoolExecutor(max_workers=8)
    futures = {pool.submit(fn, spec): spec for spec in specs}
    results = []
    try:
        for future in as_completed(futures, timeout=timeout):
            try:
                results.append((futures[future], future.result()))
            except Exception:
                continue
    except TimeoutError:
        pass
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _fetch_sina_industries(http_get=None):
    """新浪行业成分。缺失行业保持未分类，不根据股票名称猜测。"""
    get = http_get or _get
    response = get("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php")
    response.raise_for_status()
    response.encoding = "gbk"
    raw = response.text
    catalog = json.loads(raw[raw.index("{"):].strip().rstrip(";"))
    specs = []
    for value in catalog.values():
        fields = value.split(",")
        node, name, count = fields[:3]
        for page in range(1, math.ceil(int(count) / 100) + 1):
            specs.append((node, name, page))

    def load(spec):
        node, _, page = spec
        res = get(API + "Market_Center.getHQNodeData", params={
            "node": node, "page": page, "num": 100, "sort": "symbol", "asc": 1})
        res.raise_for_status()
        rows = res.json()
        return rows if isinstance(rows, list) else []

    mapping = {}
    for (_, name, _), rows in _parallel(specs, load):
        for row in rows:
            code = str(row.get("code", ""))
            if len(code) == 6 and code.isdigit():
                mapping[code] = name
    return mapping


def fetch_industries(http_get=None):
    """优先使用东方财富当前行业分类，失败时回退新浪行业。"""
    get = http_get or _get

    def load(page):
        response = get("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": page, "pz": 100, "po": 0, "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f12,f100", "np": 1})
        response.raise_for_status()
        data = response.json().get("data") or {}
        rows = data.get("diff") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not rows:
            raise ValueError("行业页为空")
        return int(data.get("total", 0)), rows

    try:
        total, first = load(1)
        if not 0 < total < 20000:
            raise ValueError("无效行业数量")
        parts = _parallel(range(2, math.ceil(total / 100) + 1), load)
        rows = first + [row for _, (_, part) in parts for row in part]
        mapping = {str(row["f12"]): str(row["f100"]) for row in rows
                   if row.get("f12") and row.get("f100") not in (None, "", "-")}
        if mapping:
            return mapping, "东方财富行业"
    except Exception:
        pass
    return _fetch_sina_industries(http_get), "新浪行业"


def fetch_quotes(http_get=None):
    """分页采集沪深北行情，市值从万元转为元，返回实际覆盖及失败页数。"""
    get = http_get or _get
    res = get(API + "Market_Center.getHQNodeStockCount", params={"node": "hs_a"})
    res.raise_for_status()
    expected = int(res.json())
    if not 0 < expected < 20000:
        raise ValueError("行情源返回了无效的股票数量")
    pages = list(range(1, math.ceil(expected / 100) + 1))

    def load(page):
        response = get(API + "Market_Center.getHQNodeData", params={
            "node": "hs_a", "page": page, "num": 100, "sort": "symbol", "asc": 1})
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError("行情页为空")
        return rows

    parts = _parallel(pages, load)
    rows = [row for _, part in parts for row in part]
    if not rows:
        raise ValueError("行情源暂时没有返回可用数据")
    df = pd.DataFrame(rows).rename(columns={
        "code": "代码", "name": "名称", "trade": "最新价", "changepercent": "涨跌幅",
        "amount": "成交额", "mktcap": "总市值", "nmc": "流通市值",
        "turnoverratio": "换手率", "ticktime": "行情时间",
    })
    for field in ("总市值", "流通市值"):
        if field in df:
            df[field] = pd.to_numeric(df[field], errors="coerce") * 10000
    df = normalize_quotes(df)
    return df, {"source": "新浪财经", "expected": expected,
                "failed_pages": len(pages) - len(parts),
                "fetched_at": datetime.now(CN_TZ).isoformat(timespec="seconds")}


def board_of(code):
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith("60"):
        return "沪市主板"
    if code.startswith("00"):
        return "深市主板"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "北交所"
    return "其他"


def normalize_quotes(frame, industries=None):
    columns = ["代码", "名称", *NUMERIC, "行业", "市场", "行情时间"]
    if frame is None or frame.empty or not {"代码", "名称"}.issubset(frame.columns):
        return pd.DataFrame(columns=columns)
    df = frame.copy()
    df["代码"] = df["代码"].astype(str).str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)
    df = df[df["代码"].str.fullmatch(r"\d{6}", na=False)].copy()
    df["名称"] = df["名称"].fillna(df["代码"]).astype(str)
    for field in NUMERIC:
        values = df[field] if field in df else pd.Series(float("nan"), index=df.index)
        df[field] = pd.to_numeric(values, errors="coerce").replace([float("inf"), -float("inf")], float("nan"))
    df = df[(df["最新价"] > 0) & df["涨跌幅"].notna()].copy()
    df["市场"] = df["代码"].map(board_of)
    df = df[df["市场"] != "其他"].copy()
    if industries is not None:
        df["行业"] = df["代码"].map(industries)
    elif "行业" not in df:
        df["行业"] = "未分类"
    df["行业"] = df["行业"].fillna("未分类").replace("", "未分类")
    if "行情时间" not in df:
        df["行情时间"] = ""
    return df.drop_duplicates("代码", keep="last").reset_index(drop=True)[columns]


def filter_quotes(frame, market="全部A股", industries=(), query="", exclude_st=True,
                  direction="全部", min_cap=0):
    df = frame.copy()
    if market != "全部A股":
        df = df[df["市场"] == market]
    if industries:
        df = df[df["行业"].isin(industries)]
    if exclude_st:
        df = df[~df["名称"].str.contains(r"ST|退", case=False, na=False)]
    if query.strip():
        term = query.strip()
        df = df[df["代码"].str.contains(term, regex=False) |
                df["名称"].str.contains(term, case=False, regex=False)]
    if direction == "仅上涨":
        df = df[df["涨跌幅"] > 0]
    elif direction == "仅下跌":
        df = df[df["涨跌幅"] < 0]
    if min_cap:
        df = df[df["总市值"] >= min_cap * 1e8]
    return df.reset_index(drop=True)


def market_stats(frame):
    n = len(frame)
    up = int((frame["涨跌幅"] > 0).sum())
    down = int((frame["涨跌幅"] < 0).sum())
    return {"count": n, "up": up, "down": down, "flat": n - up - down,
            "up_ratio": up / n * 100 if n else 0,
            "median": float(frame["涨跌幅"].median()) if n else 0,
            "amount": float(frame["成交额"].sum())}


def sector_stats(frame):
    if frame.empty:
        return []
    grouped = frame.groupby("行业", sort=False)
    result = []
    for name, group in grouped:
        caps = group["总市值"].clip(lower=0).fillna(0)
        total = float(caps.sum())
        change = float((group["涨跌幅"] * caps).sum() / total) if total else float(group["涨跌幅"].mean())
        result.append({"name": name, "change": change, "count": len(group), "cap": total})
    return sorted(result, key=lambda item: item["cap"], reverse=True)


def map_payload(frame, area="总市值"):
    if area not in AREA_FIELDS:
        raise ValueError("不支持的面积指标")
    df = frame.copy()
    weights = pd.Series(1.0, index=df.index) if area == "等权" else df[area]
    valid = weights.notna() & (weights > 0)
    omitted = int((~valid).sum())
    df = df[valid].copy()
    df["weight"] = weights[valid]
    groups = []
    for sector in sector_stats(df):
        subset = df[df["行业"] == sector["name"]].sort_values(["weight", "代码"], ascending=[False, True])
        stocks = []
        for row in subset.to_dict("records"):
            stocks.append({"code": row["代码"], "name": row["名称"], "change": row["涨跌幅"],
                           "price": row["最新价"], "weight": row["weight"],
                           "cap": None if pd.isna(row["总市值"]) else row["总市值"],
                           "amount": None if pd.isna(row["成交额"]) else row["成交额"]})
        groups.append({**sector, "weight": float(subset["weight"].sum()), "stocks": stocks})
    groups.sort(key=lambda item: item["weight"], reverse=True)
    return groups, omitted


def format_amount(value):
    if value is None or not math.isfinite(float(value)):
        return "—"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f} 万亿"
    if abs(value) >= 1e8:
        return f"{value / 1e8:,.0f} 亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:,.0f} 万"
    return f"{value:,.0f}"
