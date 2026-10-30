"""Bounded subprocess adapter to the explicit-cookie, read-only Twitter gateway."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ENGINE = "agent-reach-twitter"
_CACHE: dict[str, str] = {}
_LOCK = threading.Lock()


def _command() -> list[str]:
    python = ROOT / ".astock" / "agent-reach-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise RuntimeError("twitter_dependency_missing")
    return [str(python), str(ROOT / "scripts" / "agent_reach_twitter.py")]


def _run(action: str, query: str = "", count: int = 10) -> dict:
    argv = [*_command(), action]
    if query:
        argv += [query, "--max", str(max(1, min(int(count), 20)))]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                timeout=5 if action == "status" else 27, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        raise RuntimeError("twitter_timeout") from None
    except OSError:
        raise RuntimeError("twitter_command_failed") from None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("twitter_invalid_output") from None
    if not isinstance(payload, dict):
        raise RuntimeError("twitter_invalid_output")
    if result.returncode or not payload.get("ok"):
        label = payload.get("error", "command_failed")
        allowed = {"not_configured", "not_authenticated", "rate_limited", "not_found",
                   "network_error", "query_id_error", "api_error", "dependency_missing",
                   "configuration_or_backend_error", "invalid_input"}
        raise RuntimeError("twitter_" + (label if label in allowed else "command_failed"))
    return payload


def status() -> dict:
    """Local installation/configuration only. Never a network/login probe."""
    try:
        return _run("status")
    except RuntimeError as error:
        return {"installed": False, "configured": False, "verified": False, "error": str(error)}


def targeted_query(query: str) -> str:
    """Only route explicitly Twitter-only site queries; don't repurpose general searches."""
    hosts = re.findall(r"\bsite:([^\s)]+)", query or "", flags=re.I)
    if not hosts or any(h.lower() not in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} for h in hosts):
        return ""
    return re.sub(r"\bsite:(?:www\.)?(?:x\.com|twitter\.com)\b", "", query, flags=re.I).strip()


def tweet_id(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or parts.hostname not in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
            return ""
        match = re.fullmatch(r"/(?:[A-Za-z0-9_]+|i/web)/status/(\d{1,25})/?", parts.path)
        return match.group(1) if match else ""
    except ValueError:
        return ""


def _body(item: dict) -> str:
    text = str(item.get("text") or "").strip()
    article = str(item.get("articleText") or "").strip()
    return text + ("\n\n" + article if article and article != text else "")


def _cache(items: list) -> list[tuple]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        author = item.get("author") or {}
        name = str(author.get("screenName") or "") if isinstance(author, dict) else ""
        body = _body(item)
        if not re.fullmatch(r"\d{1,25}", ident) or not re.fullmatch(r"[A-Za-z0-9_]{1,50}", name) or not body:
            continue
        url = f"https://x.com/{name}/status/{ident}"
        published = str(item.get("createdAtISO") or item.get("createdAt") or "")
        with _LOCK:
            if len(_CACHE) >= 400:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[ident] = f"作者：@{name}\n发布时间：{published or '未知'}\n{body[:20000]}"
        rows.append((f"@{name}：{body[:90]}", url, body[:2400], published, ENGINE))
    return rows


def search(query: str, timelimit: str = "w", limit: int = 10) -> list[tuple]:
    native = targeted_query(query)
    if not native:
        return []
    days = {"d": 1, "w": 7, "m": 30}.get(timelimit)
    if re.fullmatch(r"d[1-9]\d{0,2}", str(timelimit)):
        days = int(timelimit[1:])
    now = datetime.now(timezone.utc)
    if days and not re.search(r"\bsince:", native):
        native += " since:" + (now - timedelta(days=days)).strftime("%Y-%m-%d")
    items = []
    for item in _run("search", native, limit).get("data") or []:
        if not isinstance(item, dict):
            continue
        try:
            published = datetime.fromisoformat(str(item.get("createdAtISO") or "").replace("Z", "+00:00"))
            published = published.replace(tzinfo=timezone.utc) if not published.tzinfo else published
        except (ValueError, TypeError):
            published = None
        if published and (published > now or (days and published < now - timedelta(days=days))):
            continue
        items.append(item)
    return _cache(items)


def read_page(url: str) -> str:
    ident = tweet_id(url)
    if not ident:
        raise RuntimeError("twitter_invalid_url")
    with _LOCK:
        cached = _CACHE.get(ident)
    if cached:
        return cached
    _cache(_run("tweet", ident, 1).get("data") or [])
    with _LOCK:
        text = _CACHE.get(ident)
    if not text:
        raise RuntimeError("twitter_original_unavailable")
    return text
