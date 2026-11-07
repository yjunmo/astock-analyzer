"""新闻质量排序与近似去重。

采集层负责“尽量找到”，本模块负责“哪些值得先看”。评分只用于排序，
不判断利好或利空；模型研判仍必须引用具体条目。
"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional


SOURCE_WEIGHT = {
    "cninfo": 100, "sse": 100, "szse": 100,
    "reuters": 93, "xinhua": 92, "bloomberg": 91, "intl": 88,
    "cls": 86, "stcn": 84, "caixin": 84, "yicai": 82,
    "research": 78, "em_news": 74, "flash": 72, "sina": 70,
    "web": 58, "theme": 56,
    "xueqiu": 40, "guba": 34, "weibo": 33, "x": 32,
    "reddit": 28, "stocktwits": 27,
}

HIGH_IMPACT = (
    "业绩", "财报", "年报", "季报", "预告", "快报", "分红", "回购", "增持",
    "减持", "并购", "重组", "停牌", "复牌", "问询", "处罚", "诉讼", "中标",
    "订单", "产能", "涨价", "降价", "新品", "发布", "获批", "监管", "政策",
    "央行", "美联储", "加息", "降息", "通胀", "非农", "关税", "制裁", "战争",
    "opec", "earnings", "guidance", "acquisition", "merger", "tariff",
    "sanction", "rate hike", "rate cut", "inflation", "approval",
)

GENERIC_TITLES = (
    "stocks", "rates & bonds", "markets", "market news", "latest news",
    "homepage", "breaking news", "财经新闻", "股票频道", "行情中心",
)

NAVIGATION_PATTERNS = (
    "股票股价_股价行情_讨论_资讯_财报_数据报告",
    "股票行情_最新消息_今日走势",
    "quote stock price news history",
)


def _clean_title(title: str) -> str:
    text = (title or "").lower()
    text = re.sub(r"\s*[-_|]\s*(reuters|bloomberg\.com|ft\.com|财联社|东方财富网).*$",
                  "", text, flags=re.I)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)
    return text[:120]


def _date_of(item, now: datetime) -> Optional[datetime]:
    try:
        from astock.news.news_intel import parse_published
        return parse_published(item.published, item.title, item.snippet, now=now)
    except Exception:
        return None


def is_noise(item) -> bool:
    title = (getattr(item, "title", "") or "").strip()
    clean = _clean_title(title)
    if len(clean) < 5:
        return True
    low = title.lower().strip(" -_|:")
    return (any(low == x or low.startswith(x + " -") for x in GENERIC_TITLES)
            or any(x in low for x in NAVIGATION_PATTERNS))


def quality_breakdown(item, *, name: str = "", code6: str = "",
                      now: Optional[datetime] = None, lookback_days: int = 7,
                      global_mode: bool = False) -> dict:
    now = now or datetime.now()
    channel = getattr(item, "channel", "") or "web"
    parts = {"source_authority": float(SOURCE_WEIGHT.get(channel, 50)),
             "entity_relevance": 0.0, "event_importance": 0.0,
             "verification": 0.0, "freshness": 0.0,
             "noise_penalty": 0.0, "social_penalty": 0.0}
    title = (getattr(item, "title", "") or "").lower()
    blob = f"{title} {getattr(item, 'snippet', '')}".lower()

    if name or code6:
        title_hit = ((name and name.lower() in title)
                     or (code6 and code6 in title))
        body_hit = ((name and name.lower() in blob)
                    or (code6 and code6 in blob))
        if title_hit:
            parts["entity_relevance"] = 22.0
        elif body_hit:
            # 正文顺带提及不等于文章主题就是该公司。
            parts["entity_relevance"] = -12.0
        else:
            parts["entity_relevance"] = -30.0
    if global_mode:
        parts["entity_relevance"] += 3.0
    if any(k in blob for k in HIGH_IMPACT):
        parts["event_importance"] = 10.0
    if getattr(item, "verified", "") == "official":
        parts["verification"] = 10.0

    dt = _date_of(item, now)
    if dt is not None:
        age = max((now - dt).total_seconds() / 86400.0, 0.0)
        parts["freshness"] = max(0.0, 18.0 * (1.0 - age / max(lookback_days, 1)))
    else:
        parts["freshness"] = -4.0
    if is_noise(item):
        parts["noise_penalty"] = -60.0
    if channel in {"guba", "weibo", "x", "reddit", "stocktwits"}:
        parts["social_penalty"] = -6.0
    raw = sum(parts.values())
    parts["raw_total"] = round(raw, 2)
    parts["total"] = int(round(max(0, min(raw, 140))))
    return parts


def quality_score(item, *, name: str = "", code6: str = "",
                  now: Optional[datetime] = None, lookback_days: int = 7,
                  global_mode: bool = False) -> int:
    return int(quality_breakdown(item, name=name, code6=code6, now=now,
                                 lookback_days=lookback_days,
                                 global_mode=global_mode)["total"])


def _similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return min(len(a), len(b)) >= 10
    return SequenceMatcher(None, a, b).ratio() >= 0.72


def rank_and_dedup(items: list, *, name: str = "", code6: str = "",
                   now: Optional[datetime] = None, lookback_days: int = 7,
                   global_mode: bool = False, limit: Optional[int] = None,
                   preserve_reports: bool = False) -> list:
    """权威源优先排序后做近似标题去重，转载新闻保留质量最高的一条。"""
    now = now or datetime.now()
    scored = []
    for item in items or []:
        item.quality_breakdown = quality_breakdown(
            item, name=name, code6=code6, now=now,
            lookback_days=lookback_days, global_mode=global_mode)
        item.quality_score = int(item.quality_breakdown["total"])
        item.quality_label = (
            "重点" if item.quality_score >= 92 else
            "关注" if item.quality_score >= 72 else "一般")
        scored.append(item)
    scored.sort(key=lambda x: (-int(getattr(x, "quality_score", 0)),
                               _clean_title(getattr(x, "title", ""))))

    out, titles, urls = [], [], set()
    for item in scored:
        url = (getattr(item, "url", "") or "").split("?")[0].rstrip("/").lower()
        if preserve_reports:
            from astock.search.search_tools import _canonical_url
            url = _canonical_url(getattr(item, "url", "") or "")
        title = _clean_title(getattr(item, "title", ""))
        if url and url in urls:
            continue
        similar_idx = next((i for i, old in enumerate(titles)
                            if _similar(title, old)), None)
        if similar_idx is not None and not preserve_reports:
            representative = out[similar_idx]
            representative.cluster_size = int(
                getattr(representative, "cluster_size", 1) or 1) + 1
            src = (getattr(item, "source_name", "")
                   or getattr(item, "channel", "") or "其他来源")
            related = list(getattr(representative, "related_sources", []) or [])
            if src not in related:
                related.append(src)
            representative.related_sources = related[:8]
            continue
        if is_noise(item):
            continue
        out.append(item)
        titles.append(title)
        if url:
            urls.add(url)
        if limit and len(out) >= limit:
            break
    return out


def evidence_text(items: list, limit: int = 12) -> tuple[str, list]:
    """把新闻变成带稳定编号的模型证据块，同时返回 UI 来源列表。"""
    lines, sources = [], []
    for idx, item in enumerate((items or [])[:limit], 1):
        pub = getattr(item, "published", "") or "时间未知"
        source = getattr(item, "source_name", "") or getattr(item, "channel", "")
        title = (getattr(item, "title", "") or "").strip()
        body = getattr(item, "body", "") if getattr(item, "content_status", "") == "read" else ""
        snippet = (body or getattr(item, "snippet", "") or "").strip()[:1000]
        status = getattr(item, "content_status", "not_read")
        lines.append(f"[N{idx}] {pub}｜{source}｜{title}｜原文状态={status}｜{snippet}")
        url = getattr(item, "url", "") or ""
        if url:
            sources.append((f"[N{idx}] {title[:70]}", url))
    return ("\n".join(lines) if lines else "（未取得近期新闻证据）", sources)
