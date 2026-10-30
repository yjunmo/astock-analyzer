"""把共享 Agent 研究结果转换成新闻条目，不丢失原文和核验状态。"""
from __future__ import annotations

from typing import Callable, Optional

from search_workflow import SearchBudget, research_search


def search_news(question: str, *, config: Optional[dict] = None, scope=None,
                seeds=None, initial_items=None, freshness="w", search_fn=None,
                read_fn=None, decision_fn=None, on_progress=None, budget=None):
    from news_intel import NewsItem, channel_label, classify_channel
    from news_sectors import tag_item_sectors
    initial = [{"title": it.title, "url": it.url, "snippet": it.snippet,
                "published": it.published, "engine": it.channel,
                "body": getattr(it, "body", ""), "topics": it.sectors}
               for it in initial_items or [] if it.url]
    # 可注入的搜索器用于离线测试/独立集成；没有显式配置时不会额外调用外部模型或阅读器。
    if search_fn is not None and config is None:
        config = {}
        read_fn = read_fn or (lambda url: "读取失败：未提供原文读取器")
    report = research_search(question, config=config, scope=scope, seeds=seeds,
                             seed_sources=initial, freshness=freshness,
                             search_fn=search_fn, read_fn=read_fn, decision_fn=decision_fn,
                             on_progress=on_progress, budget=budget)
    # research 已是“券商研报”渠道 ID；研究循环状态只放在 report，不能冒充研报渠道。
    items, status = [], {}
    for doc in report.sources:
        if doc.get("relevant") is False and (scope or {}).get("information_policy") != "broad":
            continue
        channel = classify_channel(doc["url"], doc["engine"])
        from news_intel import CHANNELS
        if channel not in CHANNELS:
            channel = "web"
        topics = doc["topics"] or tag_item_sectors(doc["title"], doc["snippet"] + " " + doc["excerpt"])
        items.append(NewsItem(channel=channel, title=doc["title"], url=doc["url"],
                              snippet=doc["snippet"], published=doc["published"],
                              source_name=channel_label(channel), sectors=topics,
                              body=doc["excerpt"], source_id=doc["source_id"],
                              content_status=doc["read_status"]))
        status[channel] = "ok"
    for event in report.trace:
        if event["stage"] == "search" and event["status"] == "failed":
            status.setdefault("web", "error:" + event.get("error", "unknown"))
    return items, status, report.to_dict()
