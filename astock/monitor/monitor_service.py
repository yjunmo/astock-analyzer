"""独立预警服务：规则评估、幂等站内通知与可选 Webhook。"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from typing import Optional

import requests
import pandas as pd

from astock.research import research_store as store


RULE_LABELS = {
    "price_above": "价格上穿", "price_below": "价格下穿",
    "pct_change": "涨跌幅", "volume_ratio": "量比",
    "technical": "技术信号", "financial": "财务指标",
    "news_keyword": "新闻关键词", "event_type": "事件类型",
    "thesis_due": "假设复核到期", "research_due": "AI结论复核到期",
    "thesis_invalidation": "假设失效线索",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_rule(symbol: str, name: str, rule_type: str, threshold: str = "",
                operator: str = "contains", severity: str = "medium",
                cooldown_minutes: int = 60, project_id: Optional[int] = None) -> int:
    if rule_type not in RULE_LABELS:
        raise ValueError("不支持的预警类型")
    now = _now()
    with store.connection() as conn:
        cur = conn.execute("""INSERT INTO alert_rules(project_id,symbol,name,rule_type,
          operator,threshold,severity,enabled,cooldown_minutes,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,1,?,?,?)""",
                           (project_id, str(symbol or "").zfill(6) if symbol else "",
                            name, rule_type, operator, str(threshold), severity,
                            max(1, int(cooldown_minutes)), now, now))
        return int(cur.lastrowid)


def list_rules(symbol: str = "", enabled_only: bool = False) -> list[dict]:
    where, params = [], []
    if symbol:
        where.append("symbol=?"); params.append(str(symbol).zfill(6))
    if enabled_only:
        where.append("enabled=1")
    sql = "SELECT * FROM alert_rules" + ((" WHERE " + " AND ".join(where)) if where else "")
    sql += " ORDER BY enabled DESC,id DESC"
    with store.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_rule(rule_id: int, *, enabled: Optional[bool] = None,
                threshold: Optional[str] = None) -> None:
    sets, params = [], []
    if enabled is not None:
        sets.append("enabled=?"); params.append(int(enabled))
    if threshold is not None:
        sets.append("threshold=?"); params.append(str(threshold))
    if not sets:
        return
    sets.append("updated_at=?"); params.append(_now()); params.append(int(rule_id))
    with store.connection() as conn:
        conn.execute("UPDATE alert_rules SET " + ",".join(sets) + " WHERE id=?", params)


def list_notifications(status: str = "", limit: int = 200) -> list[dict]:
    with store.connection() as conn:
        if status:
            rows = conn.execute("SELECT * FROM notifications WHERE status=? ORDER BY id DESC LIMIT ?",
                                (status, int(limit))).fetchall()
        else:
            rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?",
                                (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def set_notification_status(notification_id: int, status: str) -> None:
    if status not in {"unread", "acknowledged", "ignored", "resolved"}:
        raise ValueError("通知状态无效")
    with store.connection() as conn:
        conn.execute("UPDATE notifications SET status=?,updated_at=? WHERE id=?",
                     (status, _now(), int(notification_id)))


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare(value: float, operator: str, threshold: float) -> bool:
    return {">": value > threshold, ">=": value >= threshold,
            "<": value < threshold, "<=": value <= threshold,
            "==": value == threshold}.get(operator, False)


def _evaluate(rule: dict, cache: dict) -> Optional[dict]:
    symbol, typ, raw = rule["symbol"], rule["rule_type"], rule["threshold"]
    if typ == "thesis_due":
        thesis = store.latest_thesis(symbol)
        if thesis and thesis.get("next_review") and thesis["next_review"] <= date.today().isoformat():
            return {"value": thesis["next_review"], "message": "投资假设已到强制复核日"}
        return None
    if typ == "research_due":
        if raw and raw <= date.today().isoformat():
            return {"value": raw, "message": f"AI 研究结论已到复核日 {raw}"}
        return None
    if typ in {"news_keyword", "thesis_invalidation", "event_type"}:
        with store.connection() as conn:
            if typ in {"news_keyword", "thesis_invalidation"}:
                row = conn.execute("""SELECT title,url,
                  COALESCE(NULLIF(published,''),first_seen) AS last_seen FROM evidence WHERE symbol=?
                  AND (title LIKE ? OR snippet LIKE ?) ORDER BY last_seen DESC LIMIT 1""",
                                   (symbol, f"%{raw}%", f"%{raw}%")).fetchone()
            else:
                row = conn.execute("""SELECT title,'' AS url,latest_at AS last_seen FROM events
                  WHERE symbol=? AND event_type=? ORDER BY latest_at DESC LIMIT 1""",
                                   (symbol, raw)).fetchone()
        if row:
            prefix = "发现可能触发失效条件的线索：" if typ == "thesis_invalidation" else ""
            return {"value": row["title"], "message": prefix + row["title"],
                    "evidence_url": row["url"], "event_time": row["last_seen"]}
        return None
    if symbol not in cache:
        from astock.market import data_fetcher as dtf
        try:
            snap = dtf.fetch_realtime(dtf.normalize_symbol(symbol))
        except Exception:
            snap = {}
        cache[symbol] = {"snap": snap}
    snap = cache[symbol]["snap"]
    if typ in {"price_above", "price_below", "pct_change"}:
        price = _float(snap.get("price")); prev = _float(snap.get("prev_close"))
        target = _float(raw)
        if price is None or target is None:
            return None
        if typ == "price_above" and price >= target:
            return {"value": price, "message": f"最新价 {price:.2f} ≥ {target:.2f}"}
        if typ == "price_below" and price <= target:
            return {"value": price, "message": f"最新价 {price:.2f} ≤ {target:.2f}"}
        pct = (price / prev - 1) * 100 if prev else None
        if typ == "pct_change" and pct is not None and _compare(abs(pct), rule["operator"], target):
            return {"value": pct, "message": f"当前涨跌幅 {pct:+.2f}%"}
        return None
    if typ in {"volume_ratio", "technical"}:
        if "df" not in cache[symbol]:
            from astock.market import data_fetcher as dtf
            from astock.analysis.indicators import compute_all
            try:
                raw_df = dtf.get_history(dtf.normalize_symbol(symbol), "daily", "qfq")
                cache[symbol]["df"] = compute_all(raw_df, period="daily")
            except Exception:
                cache[symbol]["df"] = None
        df = cache[symbol]["df"]
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        if typ == "volume_ratio":
            val, target = _float(last.get("vol_ratio")), _float(raw)
            if val is not None and target is not None and _compare(val, rule["operator"], target):
                return {"value": val, "message": f"量比 {val:.2f} {rule['operator']} {target:.2f}"}
        else:
            signal = raw.lower()
            close, ma20, ma60 = map(_float, (last.get("close"), last.get("ma20"), last.get("ma60")))
            hit = (signal == "bullish" and None not in (close, ma20, ma60) and close > ma20 > ma60)
            hit = hit or (signal == "bearish" and None not in (close, ma20, ma60) and close < ma20 < ma60)
            if hit:
                return {"value": signal, "message": f"技术结构触发 {signal}"}
        return None
    if typ == "financial":
        # threshold 格式：roe>=15 / debt_ratio<=60
        m = re.match(r"([a-z_]+)\s*(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)", raw)
        if not m:
            return None
        from astock.market.fundamentals import build_fundamentals, fetch_financial_abstract, fetch_financial_indicator
        try:
            data = build_fundamentals(fetch_financial_indicator(symbol),
                                      fetch_financial_abstract(symbol))
            metric = m.group(1)
            # 基本面快照按语义分组；同时保留扁平 latest 兼容第三方扩展。
            val = _float((data.get("latest") or {}).get(metric))
            if val is None:
                for group in ("profit", "growth", "solvency", "cashflow", "valuation"):
                    val = _float((data.get(group) or {}).get(metric))
                    if val is not None:
                        break
        except Exception:
            val = None
        target = float(m.group(3))
        if val is not None and _compare(val, m.group(2), target):
            return {"value": val, "message": f"{m.group(1)}={val:.2f} {m.group(2)} {target:.2f}"}
    return None


def _notify(rule: dict, hit: dict, send_webhook: bool) -> bool:
    stamp = str(hit.get("event_time") or datetime.now().strftime("%Y-%m-%d %H:%M"))
    bucket = int(time.time() // max(int(rule["cooldown_minutes"]) * 60, 60))
    dedup = f"{rule['id']}|{hit.get('value')}|{stamp}|{bucket}"
    dedup_key = __import__("hashlib").sha256(dedup.encode()).hexdigest()[:32]
    title = f"{rule['name'] or rule['symbol']} · {RULE_LABELS.get(rule['rule_type'], rule['rule_type'])}"
    payload = {"type": rule["rule_type"], "title": title, "symbol": rule["symbol"],
               "message": hit.get("message", ""), "severity": rule["severity"],
               "value": hit.get("value"), "evidence_url": hit.get("evidence_url", ""),
               "triggered_at": _now()}
    with store.connection() as conn:
        try:
            cur = conn.execute("""INSERT INTO notifications(alert_rule_id,dedup_key,symbol,title,
              message,severity,status,evidence_url,payload_json,triggered_at,updated_at)
              VALUES(?,?,?,?,?,?,'unread',?,?,?,?)""",
                               (rule["id"], dedup_key, rule["symbol"], title,
                                hit.get("message", ""), rule["severity"],
                                hit.get("evidence_url", ""), json.dumps(payload, ensure_ascii=False),
                                payload["triggered_at"], payload["triggered_at"]))
        except Exception:
            return False
        notification_id = int(cur.lastrowid)
    webhook_status = "disabled"
    if send_webhook and os.environ.get("ASTOCK_WEBHOOK_URL", "").strip():
        webhook_status = _send_webhook(payload)
    with store.connection() as conn:
        conn.execute("UPDATE notifications SET webhook_status=? WHERE id=?",
                     (webhook_status, notification_id))
        conn.execute("UPDATE alert_rules SET last_triggered=?,last_checked=? WHERE id=?",
                     (_now(), _now(), rule["id"]))
    return True


def _send_webhook(payload: dict) -> str:
    url = os.environ.get("ASTOCK_WEBHOOK_URL", "").strip()
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("ASTOCK_WEBHOOK_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last = "error"
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                return "sent"
            last = f"http_{resp.status_code}"
        except Exception as e:
            last = type(e).__name__
        if attempt < 2:
            time.sleep(.5 * (2 ** attempt))
    return f"failed:{last}"


def _rule_due(rule: dict) -> bool:
    """价格/技术五分钟一次，公告/新闻/财务/复核三十分钟一次。"""
    if not rule.get("last_checked"):
        return True
    try:
        elapsed = (datetime.now() - datetime.strptime(rule["last_checked"],
                                                       "%Y-%m-%d %H:%M:%S")).total_seconds()
    except (TypeError, ValueError):
        return True
    fast = rule["rule_type"] in {"price_above", "price_below", "pct_change",
                                  "volume_ratio", "technical"}
    return elapsed >= (300 if fast else 1800)


def auto_review_due_runs() -> dict:
    """到期结论按实际/超额收益自动初评；用户仍可在页面覆写。"""
    from astock.research.research_services import list_research_runs
    due = list_research_runs(due_only=True, limit=100)
    reviewed, errors = 0, []
    for run in due:
        try:
            from astock.market import data_fetcher as dtf
            from astock.research.research_analytics import fetch_benchmark
            df = dtf.get_history(dtf.normalize_symbol(run["symbol"]), "daily", "qfq")
            if df is None or df.empty or not run.get("baseline_price"):
                raise ValueError("行情或生成时价格缺失")
            current = float(df["close"].iloc[-1])
            actual = (current / float(run["baseline_price"]) - 1) * 100
            benchmark_df, benchmark = fetch_benchmark(run["symbol"])
            benchmark_return = None
            if benchmark_df is not None and not benchmark_df.empty and run.get("baseline_date"):
                b = benchmark_df[pd.to_datetime(benchmark_df["date"]) >= pd.Timestamp(run["baseline_date"])]
                if len(b) >= 2:
                    benchmark_return = (float(b["close"].iloc[-1]) /
                                        float(b["close"].iloc[0]) - 1) * 100
            excess = actual - benchmark_return if benchmark_return is not None else None
            try:
                structured = json.loads(run.get("structured_json") or "{}")
            except Exception:
                structured = {}
            label = structured.get("direction") or ""
            direction = {"看多": 1, "看空": -1, "中性": 0}.get(label)
            score_return = excess if excess is not None else actual
            if direction is None:
                outcome = "inconclusive"
                reason = "归档结论缺少明确方向，无法自动判定"
            elif direction == 0:
                outcome = ("correct" if abs(score_return) <= 2 else
                           "partial" if abs(score_return) <= 5 else "wrong")
                reason = f"中性判断对应期内超额波动 {score_return:+.2f}%"
            elif abs(score_return) < 2:
                outcome = "partial"
                reason = f"方向尚未充分兑现，期内超额收益 {score_return:+.2f}%"
            else:
                outcome = "correct" if score_return * direction > 0 else "wrong"
                reason = ("实际方向与归档方向一致" if outcome == "correct"
                          else "实际方向与归档方向相反")
            detail = {"auto": True, "actual_return_pct": round(actual, 3),
                      "benchmark": benchmark,
                      "benchmark_return_pct": (round(benchmark_return, 3)
                                                 if benchmark_return is not None else None),
                      "excess_return_pct": round(excess, 3) if excess is not None else None,
                      "direction": label or "缺失", "reason": reason,
                      "note": "自动初评，可由用户覆写"}
            with store.connection() as conn:
                conn.execute("""UPDATE research_runs SET outcome=?,outcome_note=?,reviewed_at=?
                  WHERE id=?""", (outcome, json.dumps(detail, ensure_ascii=False), _now(), run["id"]))
            reviewed += 1
        except Exception as e:
            errors.append(f"#{run['id']}:{type(e).__name__}")
    return {"reviewed": reviewed, "errors": errors}


def scan_rules(send_webhook: bool = True, force: bool = False) -> dict:
    rules = list_rules(enabled_only=True)
    cache, triggered, errors = {}, 0, []
    checked = 0
    for rule in rules:
        if not force and not _rule_due(rule):
            continue
        try:
            checked += 1
            hit = _evaluate(rule, cache)
            if hit and _notify(rule, hit, send_webhook):
                triggered += 1
            with store.connection() as conn:
                conn.execute("UPDATE alert_rules SET last_checked=? WHERE id=?", (_now(), rule["id"]))
        except Exception as e:
            errors.append(f"#{rule['id']} {type(e).__name__}")
    reviews = auto_review_due_runs()
    maintenance = {"skipped": True}
    try:
        last = store.meta_get("last_content_hydration", "")
        due = force or not last or (datetime.now() - datetime.strptime(
            last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= 1800
        if due:
            from astock.research.research_services import hydrate_evidence_content
            maintenance = hydrate_evidence_content(limit=20)
            store.meta_set("last_content_hydration", _now())
    except Exception as e:
        maintenance = {"error": type(e).__name__}
    return {"checked": checked, "skipped": len(rules) - checked,
            "triggered": triggered, "errors": errors, "auto_reviews": reviews,
            "maintenance": maintenance,
            "finished_at": _now()}
