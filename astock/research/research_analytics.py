"""事件研究、时间轴价格异动与组合风险的纯计算/数据装配。"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Optional

import pandas as pd

from astock.research import research_store as store


WINDOWS = (1, 5, 20, 60)


def benchmark_symbol(code6: str) -> str:
    code = str(code6 or "")
    if code.startswith("300"):
        return "sz399006"       # 创业板指
    if code.startswith("688"):
        return "sh000688"       # 科创50
    if code.startswith(("0", "2")):
        return "sz399001"       # 深证成指
    if code.startswith(("4", "8", "92")):
        return "bj899050"       # 北证50
    return "sh000001"           # 上证指数


def fetch_benchmark(code6: str, ak_mod=None) -> tuple[pd.DataFrame, str]:
    """分市场指数，失败回退沪深300；再失败返回空表。"""
    if ak_mod is None:
        import akshare as ak_mod  # type: ignore
    primary = benchmark_symbol(code6)
    for symbol in (primary, "sh000300"):
        try:
            df = ak_mod.stock_zh_index_daily(symbol=symbol)
            if df is not None and not df.empty:
                out = df.rename(columns={"日期": "date", "收盘": "close"}).copy()
                out["date"] = pd.to_datetime(out["date"], errors="coerce")
                out["close"] = pd.to_numeric(out["close"], errors="coerce")
                return out.dropna(subset=["date", "close"]).sort_values("date"), symbol
        except Exception:
            continue
    return pd.DataFrame(), ""


def compute_event_study(stock_df: pd.DataFrame, event_date: str,
                        benchmark_df: Optional[pd.DataFrame] = None) -> dict:
    """事件日起严格向后计算收益，不用事件之后的信息选择事件。"""
    if stock_df is None or stock_df.empty:
        return {"ok": False, "reason": "股票行情缺失"}
    s = stock_df[["date", "close"]].copy()
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s["close"] = pd.to_numeric(s["close"], errors="coerce")
    s = s.dropna().sort_values("date").reset_index(drop=True)
    target = pd.Timestamp(event_date).normalize()
    matches = s.index[s["date"].dt.normalize() >= target]
    if len(matches) == 0:
        return {"ok": False, "reason": "事件日晚于行情数据"}
    base_idx = int(matches[0]); base_price = float(s.loc[base_idx, "close"])
    returns, excess = {}, {}
    bmap = None
    if benchmark_df is not None and not benchmark_df.empty:
        b = benchmark_df[["date", "close"]].copy()
        b["date"] = pd.to_datetime(b["date"], errors="coerce").dt.normalize()
        b["close"] = pd.to_numeric(b["close"], errors="coerce")
        bmap = b.dropna().drop_duplicates("date").set_index("date")["close"]
    base_b = None
    if bmap is not None:
        eligible = bmap.index[bmap.index >= s.loc[base_idx, "date"].normalize()]
        if len(eligible):
            base_b = float(bmap.loc[eligible[0]])
    for window in WINDOWS:
        idx = base_idx + window
        key = str(window)
        if idx >= len(s):
            returns[key] = None; excess[key] = None
            continue
        end_price = float(s.loc[idx, "close"])
        ret = (end_price / base_price - 1) * 100
        returns[key] = round(ret, 4)
        bret = None
        if bmap is not None and base_b:
            end_date = s.loc[idx, "date"].normalize()
            eligible = bmap.index[bmap.index <= end_date]
            if len(eligible):
                bret = (float(bmap.loc[eligible[-1]]) / base_b - 1) * 100
        excess[key] = round(ret - bret, 4) if bret is not None else None
    return {"ok": True, "event_trade_date": s.loc[base_idx, "date"].date().isoformat(),
            "base_price": base_price, "returns": returns, "excess": excess}


def run_event_studies(symbol: str, stock_df: pd.DataFrame,
                      events: list[dict], ak_mod=None) -> list[dict]:
    benchmark_df, benchmark = fetch_benchmark(symbol, ak_mod=ak_mod)
    out = []
    with store.connection() as conn:
        for event in events:
            event_date = (event.get("earliest_at") or event.get("latest_at") or "")[:10]
            if not event_date:
                continue
            result = compute_event_study(stock_df, event_date, benchmark_df)
            if not result.get("ok"):
                continue
            comparability = _comparability(event, stock_df, event_date)
            conn.execute("""INSERT INTO event_studies(event_id,symbol,benchmark,event_date,
              returns_json,excess_json,comparability,created_at) VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id,symbol,benchmark) DO UPDATE SET returns_json=excluded.returns_json,
              excess_json=excluded.excess_json,comparability=excluded.comparability,
              created_at=excluded.created_at""",
                         (event["event_id"], str(symbol).zfill(6), benchmark, event_date,
                          json.dumps(result["returns"]), json.dumps(result["excess"]),
                          comparability, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            out.append({**event, **result, "benchmark": benchmark,
                        "comparability": comparability})
    return out


def run_peer_event_studies(events: list[dict], limit: int = 20) -> dict:
    """补算本地同公司/同行事件样本，逐标的取历史数据且严格从事件日向后。"""
    from astock.market import data_fetcher as dtf
    completed, failed, results = 0, [], []
    histories: dict[str, pd.DataFrame] = {}
    benchmarks: dict[str, tuple[pd.DataFrame, str]] = {}
    for event in events[:limit]:
        symbol = str(event.get("symbol") or "").zfill(6)
        event_date = str(event.get("earliest_at") or event.get("latest_at") or "")[:10]
        if not symbol or not event_date:
            continue
        try:
            if symbol not in histories:
                histories[symbol] = dtf.get_history(dtf.normalize_symbol(symbol), "daily", "qfq")
                benchmarks[symbol] = fetch_benchmark(symbol)
            benchmark_df, benchmark = benchmarks[symbol]
            result = compute_event_study(histories[symbol], event_date, benchmark_df)
            if not result.get("ok"):
                failed.append(f"{symbol}:{result.get('reason')}")
                continue
            comparability = float(event.get("similarity_score") or
                                  _comparability(event, histories[symbol], event_date))
            with store.connection() as conn:
                conn.execute("""INSERT INTO event_studies(event_id,symbol,benchmark,event_date,
                  returns_json,excess_json,comparability,created_at) VALUES(?,?,?,?,?,?,?,?)
                  ON CONFLICT(event_id,symbol,benchmark) DO UPDATE SET
                  returns_json=excluded.returns_json,excess_json=excluded.excess_json,
                  comparability=excluded.comparability,created_at=excluded.created_at""",
                             (event["event_id"], symbol, benchmark, event_date,
                              json.dumps(result["returns"]), json.dumps(result["excess"]),
                              comparability, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            results.append({**event, **result, "benchmark": benchmark,
                            "comparability": comparability})
            completed += 1
        except Exception as error:
            failed.append(f"{symbol}:{type(error).__name__}")
    return {"completed": completed, "failed": failed, "results": results}


def _comparability(event: dict, df: pd.DataFrame, event_date: str) -> float:
    """同类事件比较的市场环境评分：趋势和波动可用性，各占一半。"""
    try:
        d = df.copy(); d["date"] = pd.to_datetime(d["date"])
        before = d[d["date"] < pd.Timestamp(event_date)].tail(60)
        if len(before) < 20:
            return .3
        ret60 = float(before["close"].iloc[-1] / before["close"].iloc[0] - 1)
        vol = float(pd.to_numeric(before["close"]).pct_change().std() * math.sqrt(242))
        return round(max(.2, min(.95, .8 - abs(ret60) * .5 - max(vol - .4, 0) * .3)), 3)
    except Exception:
        return .3


def ingest_price_events(symbol: str, name: str, df: pd.DataFrame,
                        pct_threshold: float = 5.0, volume_threshold: float = 2.0) -> int:
    if df is None or df.empty:
        return 0
    d = df.tail(300).copy()
    d["pct"] = pd.to_numeric(d["close"], errors="coerce").pct_change() * 100
    count = 0
    with store.connection() as conn:
        for _, row in d.iterrows():
            pct = float(row.get("pct") or 0)
            vr = float(row.get("vol_ratio") or 0)
            if abs(pct) < pct_threshold and vr < volume_threshold:
                continue
            day = pd.Timestamp(row["date"]).date().isoformat()
            title = f"{name}股价异动：{pct:+.2f}% / 量比{vr:.2f}"
            event_id = __import__("hashlib").sha256(
                f"{symbol}|price_move|{day}".encode()).hexdigest()[:24]
            conn.execute("""INSERT INTO events(event_id,symbol,name,event_type,title,summary,
              stance,impact,verified,status,earliest_at,latest_at,authority_source,source_count,
              payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id) DO UPDATE SET title=excluded.title,summary=excluded.summary,
              payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                         (event_id, str(symbol).zfill(6), name, "price_move", title,
                          f"收盘价{float(row['close']):.2f}，涨跌幅{pct:+.2f}%，量比{vr:.2f}",
                          "bull" if pct > 0 else "bear", "mid", "official", "confirmed",
                          day, day, "市场行情", 1,
                          json.dumps({"price": float(row["close"]), "pct": pct,
                                      "volume_ratio": vr}, ensure_ascii=False),
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            count += 1
    return count


def ingest_financial_report_events(symbol: str, name: str, snapshot: dict) -> int:
    """按财务接口返回的实际报告期建立财报节点，不使用抓取日冒充报告期。"""
    if not snapshot or not snapshot.get("ok"):
        return 0
    trend = snapshot.get("trend") or {}
    periods = list(trend.get("report_periods") or [])
    if not periods and snapshot.get("period") and snapshot["period"] != "未知报告期":
        periods = [snapshot["period"]]
    count = 0
    with store.connection() as conn:
        for idx, period in enumerate(periods[-12:]):
            if not period or period == "-":
                continue
            def at(key):
                values = trend.get(key) or []
                return values[idx] if idx < len(values) else None
            payload = {"revenue": at("revenue"), "profit": at("profit")}
            if period == snapshot.get("period") or len(periods) == 1:
                payload.update({"profitability": snapshot.get("profit") or {},
                                "growth": snapshot.get("growth") or {},
                                "solvency": snapshot.get("solvency") or {},
                                "cashflow": snapshot.get("cashflow") or {}})
            event_id = __import__("hashlib").sha256(
                f"{symbol}|financial_report|{period}".encode()).hexdigest()[:24]
            title = f"{name} {period} 财务报告节点"
            summary = (f"报告期 {period}；营收 {payload.get('revenue', '—')}；"
                       f"净利润 {payload.get('profit', '—')}")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""INSERT INTO events(event_id,symbol,name,event_type,title,summary,
              stance,impact,verified,status,earliest_at,latest_at,authority_source,source_count,
              payload_json,created_at,updated_at,cluster_key,earliest_source,latest_source)
              VALUES(?,?,?,?,?,?,'neutral','high','official','confirmed',?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id) DO UPDATE SET summary=excluded.summary,
              payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                         (event_id, str(symbol).zfill(6), name, "financial_report", title, summary,
                          str(period)[:10], str(period)[:10], "财务报表接口", 1,
                          json.dumps(payload, ensure_ascii=False, default=str), now, now,
                          f"financial_report|{period}", "财务报表接口", "财务报表接口"))
            count += 1
    return count


def analyze_portfolio(positions: list[dict], histories: dict[str, pd.DataFrame],
                      prices: Optional[dict[str, float]] = None,
                      market_caps: Optional[dict[str, float]] = None) -> dict:
    prices = prices or {}
    market_caps = market_caps or {}
    rows, total = [], 0.0
    returns = {}
    for pos in positions:
        code = pos["symbol"]
        df = histories.get(code)
        price = prices.get(code)
        if price is None and df is not None and not df.empty:
            price = float(df["close"].iloc[-1])
        price = float(price or 0)
        value = price * float(pos["shares"])
        pnl = (price - float(pos["cost_price"])) * float(pos["shares"])
        row = {**pos, "price": price, "market_value": value, "pnl": pnl,
               "market_cap": market_caps.get(code),
               "pnl_pct": ((price / float(pos["cost_price"]) - 1) * 100
                           if float(pos["cost_price"]) else None)}
        if df is not None and len(df) >= 20:
            close = pd.to_numeric(df["close"], errors="coerce")
            daily = close.pct_change().tail(120)
            returns[code] = pd.Series(daily.values,
                                      index=pd.to_datetime(df["date"].tail(len(daily))))
            row["momentum_60"] = ((float(close.iloc[-1] / close.iloc[-61] - 1) * 100)
                                  if len(close) > 60 else None)
            row["volatility"] = float(daily.std() * math.sqrt(242) * 100)
        rows.append(row); total += value
    for row in rows:
        row["weight"] = row["market_value"] / total * 100 if total else 0
    industry = {}
    for row in rows:
        key = row.get("industry") or "未分类"
        industry[key] = industry.get(key, 0) + row["weight"]
    aligned = pd.concat(returns, axis=1).dropna(how="all") if returns else pd.DataFrame()
    corr = aligned.corr(min_periods=30) if not aligned.empty else pd.DataFrame()
    warnings = []
    if rows and max(r["weight"] for r in rows) >= 30:
        warnings.append("单一持仓权重超过30%")
    if industry and max(industry.values()) >= 50:
        warnings.append("单一行业权重超过50%")
    if not corr.empty:
        off_diagonal = corr.copy()
        for label in set(off_diagonal.index).intersection(off_diagonal.columns):
            off_diagonal.loc[label, label] = float("nan")
        vals = off_diagonal.stack()
        if len(vals) and vals.max() >= .8:
            warnings.append("存在收益相关性高于0.8的持仓")
    valid_caps = [float(r["market_cap"]) for r in rows if r.get("market_cap")]
    valid_vols = [float(r["volatility"]) for r in rows if r.get("volatility") is not None]
    cap_median = pd.Series(valid_caps).median() if valid_caps else None
    vol_median = pd.Series(valid_vols).median() if valid_vols else None
    style = {"large_cap_weight": 0.0, "small_cap_weight": 0.0,
             "positive_momentum_weight": 0.0, "negative_momentum_weight": 0.0,
             "high_volatility_weight": 0.0, "low_volatility_weight": 0.0,
             "weighted_momentum_60": None, "weighted_volatility": None}
    momentum_sum = volatility_sum = momentum_weight = volatility_weight = 0.0
    for row in rows:
        weight = row["weight"]
        cap = row.get("market_cap")
        if cap_median is not None and cap:
            key = "large_cap_weight" if float(cap) >= cap_median else "small_cap_weight"
            style[key] += weight
            row["size_proxy"] = "大盘代理" if float(cap) >= cap_median else "小盘代理"
        momentum = row.get("momentum_60")
        if momentum is not None:
            key = "positive_momentum_weight" if momentum >= 0 else "negative_momentum_weight"
            style[key] += weight
            momentum_sum += momentum * weight; momentum_weight += weight
        volatility = row.get("volatility")
        if volatility is not None and vol_median is not None:
            key = "high_volatility_weight" if volatility >= vol_median else "low_volatility_weight"
            style[key] += weight
            volatility_sum += volatility * weight; volatility_weight += weight
    if momentum_weight:
        style["weighted_momentum_60"] = momentum_sum / momentum_weight
    if volatility_weight:
        style["weighted_volatility"] = volatility_sum / volatility_weight
    return {"rows": rows, "total_value": total, "industry_weights": industry,
            "correlation": corr, "warnings": warnings,
            "concentration": sum((r["weight"] / 100) ** 2 for r in rows),
            "style_exposure": style}
