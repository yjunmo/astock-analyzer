import json
from datetime import datetime
from html import unescape
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from astock.news.event_pulse import EventPulse, analyze_pulse, collect_pulse, visible_items
from astock.news.news_intel import NewsItem, classify_channel
from astock.news.news_quality import rank_and_dedup
from astock.news.pulse_recommendations import a_share_code, finalize_chains, shortlist
from astock.search.search_workflow import SearchBudget, SearchReport, research_search


BODY = "公司年报披露主营业务包括动力电池及储能系统。该段为离线测试正文，不构成任何实时投资事实。"


def report():
    return SearchReport("产业链测试", objective="collect", status="collected", sources=[
        {"source_id": "W1", "title": "企业业务资料", "url": "https://example.com/company",
         "engine": "test", "published": "", "body": BODY, "excerpt": BODY,
         "read_status": "read", "snippet": "业务资料", "topics": []}])


def chain():
    return {"event": "储能需求情景", "stance": "bull", "news_ids": [0, 999],
            "segments": ["材料", "电芯", "系统"], "logic": ["需求->电芯->系统交付"],
            "chain": [{"stage": "中游", "segment": "电芯", "mechanism": "需求情景传导"}],
            "risks": ["需求不兑现"], "candidates": [
                {"name": "宁德时代", "code": "300750", "role": "电芯", "material": "电池",
                 "stance": "bull", "bull_score": 9, "reason": "按需求情景推演电芯环节",
                 "catalyst": "下一公开业务披露", "risk": "价格竞争", "basis": "业务映射推演",
                 "business_sources": [{"source_id": "W1", "quote": "主营业务包括动力电池及储能系统"},
                                      {"source_id": "W999", "quote": "编造原文"}]},
                {"name": "阳光电源", "code": "300274", "role": "系统", "stance": "bull", "bull_score": 8},
                {"name": "赣锋锂业", "code": "002460", "role": "材料", "stance": "mixed", "bull_score": 7}]}


class TestBroadNews(unittest.TestCase):
    def test_source_labels_are_not_lumped_into_generic_web(self):
        self.assertEqual(classify_channel("https://english.news.cn/a.html", "exa"), "xinhua")
        self.assertEqual(classify_channel("https://www.xinhuanet.com/a.html", "exa"), "xinhua")
        self.assertEqual(classify_channel("https://www.bloomberg.com/news/articles/a", "exa"), "bloomberg")

    def test_all_reports_and_social_posts_retained_even_when_body_blocked(self):
        rows = [("行业扩产讨论消息", f"https://x.com/person/status/{i}", "行业扩产传闻", datetime.now().isoformat())
                for i in range(65)] + [("同一产业消息的另一观点", "https://www.bloomberg.com/news/a", "摘要", datetime.now().isoformat())]
        search = Mock(return_value=rows)
        pulse = collect_pulse(ak_mod=SimpleNamespace(), search_fn=search,
                              read_fn=lambda *a: "读取失败：正文受限", translate_en=False,
                              decision_fn=lambda *a: {"queries": ["产业消息"], "done": True},
                              search_budget=SearchBudget(rounds=3, queries=6, pages=1, sources=96))
        self.assertEqual(len(pulse.items), 66)
        self.assertEqual(len(visible_items(pulse)), 66)
        self.assertEqual(sum(i.channel == "x" for i in pulse.items), 65)
        self.assertEqual(pulse.search_report["status"], "collected")
        self.assertEqual(pulse.search_report["claims"], [])
        self.assertEqual(pulse.channel_status["bloomberg"], "ok")
        self.assertEqual(pulse.channel_status["xinhua"], "empty")
        self.assertNotIn("research", pulse.channel_status)

    def test_different_article_id_queries_are_not_merged(self):
        items = [NewsItem(channel="web", title="同一新闻的不同观点", url=f"https://example.com/news?id={i}") for i in (1, 2)]
        self.assertEqual(len(rank_and_dedup(items, preserve_reports=True)), 2)

    def test_required_media_are_not_skipped_on_early_done(self):
        search = Mock(return_value=[("产业讨论", "https://x.com/person/status/1", "消息")])
        result = research_search("收集消息", config={}, scope={"information_policy": "broad", "required_queries": [
            {"query": "行业 site:news.cn"}, {"query": "industry site:bloomberg.com"}]},
            search_fn=search, read_fn=lambda *a: "读取失败", decision_fn=lambda *a: {"queries": ["广泛发现"], "done": True})
        queries = [c.args[0] for c in search.call_args_list]
        self.assertIn("行业 site:news.cn", queries)
        self.assertIn("industry site:bloomberg.com", queries)
        self.assertEqual(result.status, "collected")


class TestRecommendations(unittest.TestCase):
    def test_a_share_identifiers_exclude_foreign_and_b_shares(self):
        for code in ("SH600000", "300750.SZ", "688001", "920001", "830001"):
            self.assertTrue(a_share_code(code))
        for code in ("NVDA", "00700", "HK000001", "200001", "900001", "999999"):
            self.assertEqual(a_share_code(code), "")

    def test_company_mismatch_fake_business_refs_and_duplicate_shortlist(self):
        item = chain()
        item["candidates"] += [{"name": "腾讯控股", "code": "000001", "stance": "bull", "bull_score": 10},
                               {"name": "苹果", "code": "AAPL", "stance": "bull", "bull_score": 10}]
        result = finalize_chains([item], report(), lambda codes: {"300750": "宁德时代", "000001": "平安银行"}, 1)
        self.assertEqual(len(result[0]["candidates"]), 3)
        self.assertEqual(result[0]["news_ids"], [0])
        self.assertEqual(len(result[0]["candidates"][0]["business_sources"]), 1)
        self.assertEqual(len(shortlist(result + result)), 3)

    def test_partial_search_does_not_block_news_to_a_share_recommendations(self):
        pulse = EventPulse("2026-10-07", items=[NewsItem(channel="x", title="储能市场讨论", snippet="情景素材")])
        first = {"headline": "产业情景", "summary": "按消息推演产业链", "picks": [chain()]}
        refined = {"picks": [chain()], "limitations": ["部分业务资料仅有摘要"]}
        chat = Mock(side_effect=[{"content": json.dumps(first, ensure_ascii=False)},
                                 {"content": json.dumps(refined, ensure_ascii=False)}])
        research = Mock(return_value=report())
        research.return_value.status = "partial"
        result = analyze_pulse(pulse, {"protocol": "openai"}, "model", "key", chat_fn=chat,
                               research_fn=research, stock_lookup=lambda codes: {"300750": "宁德时代", "300274": "阳光电源", "002460": "赣锋锂业"})
        self.assertTrue(result.ai_used)
        self.assertEqual(result.recommendation_status, "ready")
        self.assertEqual(len(result.recommendations), 3)
        self.assertEqual(result.recommendations[0]["code"], "300750")
        self.assertTrue(result.tech_chains[0]["segments"])
        self.assertEqual(research.call_args.kwargs["freshness"], "any")
        self.assertEqual(research.call_args.kwargs["scope"]["market"], "A股")
        self.assertTrue(research.call_args.kwargs["scope"]["required_first"])
        self.assertEqual(len(research.call_args.kwargs["scope"]["required_queries"]), 3)
        prompt = unescape(chat.call_args.args[3][-1]["content"])
        self.assertIn("主营业务包括动力电池", prompt)

    def test_refinement_failure_preserves_draft_and_news(self):
        pulse = EventPulse("2026-10-07", items=[NewsItem(channel="web", title="产业新闻")])
        chat = Mock(side_effect=[{"content": json.dumps({"picks": [chain()]}, ensure_ascii=False)}, RuntimeError("private-key")])
        result = analyze_pulse(pulse, {}, "model", "key", chat_fn=chat,
                               research_fn=Mock(side_effect=RuntimeError("private-key")), stock_lookup=lambda codes: {})
        self.assertEqual(result.recommendation_status, "draft")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(result.recommendations), 3)
        self.assertNotIn("private-key", str(result.chain_search_report))


if __name__ == "__main__":
    unittest.main()
