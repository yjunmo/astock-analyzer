"""联网搜索工具层：供 AI Agent 通过 function-calling 自主调用。

两个工具：
- web_search(query, freshness): 多引擎检索（DuckDuckGo→Bing RSS 回退），
  自动探测系统代理；输出编号条目 [n]《标题》摘要(url)
- read_page(url): 抓取网页正文（去 script/style/标签、压缩空白、截断），
  用于事实核查类深度阅读

所有失败都以文本形式返回给模型（而非抛出异常），模型可据此调整关键词重试。
"""
from __future__ import annotations

import html as _html
import os
import re
from typing import Optional

import requests

SEARCH_TIMEOUT = 8
READ_PAGE_TIMEOUT = 10
READ_PAGE_MAX_CHARS = 3000
SEARCH_RESULTS_LIMIT = 6

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "实时联网搜索。适用于：时效性信息（今日/最新）、动态数据、"
            "新闻事件、事实核查、特定来源查询。返回带编号与URL的结果列表。"
            "如首次结果不理想，可更换关键词再次调用。"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "搜索关键词，建议简洁且含专有名词"},
                "freshness": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "description": "时效过滤：day=一天内, week=一周内, month=一月内。默认 week"},
            },
            "required": ["query"],
        },
    },
}

READ_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_page",
        "description": "读取指定网页的正文文本（自动去除脚本/样式/标签）。"
                       "用于核查 web_search 结果中的原文细节。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整的 http(s) 链接"},
            },
            "required": ["url"],
        },
    },
}

ALL_TOOLS = [WEB_SEARCH_SCHEMA, READ_PAGE_SCHEMA]


# ---------------------------------------------------------------- 代理探测

def detect_proxy() -> str:
    """显式环境变量 > Windows 系统代理(注册表)。空串表示直连。"""
    for k in ("ASTOCK_SEARCH_PROXY", "HTTPS_PROXY", "https_proxy",
              "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(k)
        if v:
            return v
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enabled:
            return ""
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        server = str(server).strip()
        if ";" in server and "=" in server:
            parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
            server = parts.get("https") or parts.get("http") or ""
        if server and not server.startswith("http"):
            server = "http://" + server
        return server
    except Exception:
        return ""


# ---------------------------------------------------------------- 检索内核

_TL_MAP = {"day": "d", "week": "w", "month": "m"}


def _via_ddg(query: str, timelimit: str) -> list:
    try:
        from ddgs import DDGS          # 新包名
    except ImportError:
        from duckduckgo_search import DDGS  # 旧包名兼容
    px = detect_proxy()
    try:
        client = DDGS(timeout=SEARCH_TIMEOUT, proxy=px or None)
    except TypeError:
        client = DDGS(timeout=SEARCH_TIMEOUT)
    out = []
    with client as dd:
        try:
            it = dd.text(query, max_results=SEARCH_RESULTS_LIMIT * 2,
                         region="cn-zh", timelimit=timelimit)
        except TypeError:
            it = dd.text(query, max_results=SEARCH_RESULTS_LIMIT * 2)
        for r in it:
            out.append((str(r.get("title", ""))[:100],
                        str(r.get("href") or r.get("url") or ""),
                        str(r.get("body", ""))[:150]))
    return out


def _via_bing_rss(query: str) -> list:
    import requests
    from urllib.parse import quote
    url = ("https://www.bing.com/search?q=" + quote(query)
           + "&format=rss&setmkt=zh-CN&count=12")
    resp = requests.get(url, timeout=SEARCH_TIMEOUT,
                        headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    items = re.findall(
        r"<item>\s*<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>\s*"
        r"<link>(.*?)</link>\s*"
        r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>",
        resp.text, re.S)
    strip = lambda s: _html.unescape(re.sub(r"<[^>]+>", "", s or "").strip())  # noqa: E731
    return [(strip(t)[:100], u.strip(), strip(s)[:150])
            for t, u, s in items[:SEARCH_RESULTS_LIMIT * 2]]


def search_raw(query: str, timelimit: str = "w") -> list:
    """检索原始三元组列表 (标题, url, 摘要)。

    DuckDuckGo 抛错或返回空时回退 Bing RSS；双引擎均失败抛 RuntimeError。
    """
    tl = _TL_MAP.get(timelimit, timelimit)
    rows = []
    ddg_failed = False
    try:
        rows = _via_ddg(query, tl or "w")
    except Exception:
        ddg_failed = True
    if not rows:
        try:
            rows = _via_bing_rss(query)
        except Exception as e:
            if ddg_failed:
                raise RuntimeError(f"ddg与bing均无结果: {e}") from e
    # 去重（按 URL）
    seen, uniq = set(), []
    for t, u, b in rows:
        key = u or t
        if key and key not in seen:
            seen.add(key)
            uniq.append((t, u, b))
    return uniq


# ---------------------------------------------------------------- 工具实现

def tool_web_search(query: str, freshness: str = "week") -> str:
    query = (query or "").strip()
    if not query:
        return "搜索失败：query 为空。请提供明确的关键词。"
    try:
        rows = search_raw(query, freshness)
    except Exception as e:  # noqa: BLE001 —— 失败信息回传给模型自行调整
        return f"搜索失败（{type(e).__name__}）。建议：更换更具体的关键词后重试。"
    if not rows:
        return ("无搜索结果。建议：拆分或替换关键词、去掉引号、"
                "改用股票代码/公司全称再试。")
    lines = []
    for i, (t, u, b) in enumerate(rows[:SEARCH_RESULTS_LIMIT], 1):
        src = f"({u})" if u.startswith("http") else ""
        lines.append(f"[{i}]《{t}》{b}{src}")
    return "\n".join(lines)


def _strip_html(raw: str) -> str:
    txt = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def tool_read_page(url: str) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "读取失败：仅支持 http(s) 链接。"
    px = detect_proxy()
    proxies = {"http": px, "https": px} if px else None
    try:
        resp = requests.get(url, timeout=READ_PAGE_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"},
                            proxies=proxies)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"读取失败（{type(e).__name__}）。可尝试其他来源的同类内容。"
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype and "text" not in ctype:
        return f"读取失败：该链接非网页内容（{ctype.split(';')[0]}）。"
    text = _strip_html(resp.text)
    if len(text) < 40:
        return "读取失败：页面无可提取正文（可能需要浏览器渲染）。"
    tail = "\n…(正文过长已截断)" if len(text) > READ_PAGE_MAX_CHARS else ""
    return f"【{url}】正文节选：\n{text[:READ_PAGE_MAX_CHARS]}{tail}"


def execute_tool(name: str, args: dict) -> str:
    """Agent 循环的工具分发入口。未知工具返回说明文本。"""
    if name == "web_search":
        return tool_web_search(str(args.get("query", "")),
                               str(args.get("freshness", "week")))
    if name == "read_page":
        return tool_read_page(str(args.get("url", "")))
    return f"未知工具 {name}。可用工具：web_search, read_page。"


def extract_sources(formatted: str) -> list:
    """从 tool_web_search 的输出中解析 (标题, url) 对，用于界面来源展示。"""
    pairs = []
    for m in re.finditer(r"\[(\d+)\]《(.+?)》.*?\((https?://[^)]+)\)", formatted):
        pairs.append((m.group(2), m.group(3)))
    return pairs
