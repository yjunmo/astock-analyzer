import unittest

from astock.search.search_intelligence import entity_aliases, exchange_host, plan_stock_queries
from astock.search.search_tools import _canonical_url, keep_entity_relevant, rank_search_rows


class TestSearchIntelligence(unittest.TestCase):
    def test_exchange_and_alias_expansion(self):
        self.assertEqual(exchange_host("300750"), "szse.cn")
        self.assertEqual(exchange_host("600519"), "sse.com.cn")
        self.assertIn("CATL", entity_aliases("300750", "宁德时代"))
        queries = plan_stock_queries("300750", "宁德时代")
        self.assertTrue(any("cninfo.com.cn" in q for _, q in queries))
        self.assertTrue(any("reuters.com" in q for _, q in queries))

    def test_canonical_url_and_authority_ranking(self):
        self.assertEqual(_canonical_url("https://x.com/a?utm_source=z&id=1"),
                         "https://x.com/a?id=1")
        rows = [
            ("宁德时代讨论", "https://example.com/a", "宁德时代", ""),
            ("宁德时代公告", "https://www.cninfo.com.cn/a", "正式公告", "2026-09-26"),
        ]
        ranked = rank_search_rows(rows, "宁德时代 公告")
        self.assertIn("cninfo.com.cn", ranked[0][1])

    def test_generic_terms_cannot_replace_company_entity(self):
        rows = [
            ("其他公司最新公告", "https://example.com/other", "重大消息"),
            ("贵州茅台发布公告", "https://example.com/maotai", "600519"),
        ]
        kept = keep_entity_relevant(rows, "贵州茅台 600519 最新 公告")
        self.assertEqual(len(kept), 1)
        self.assertIn("贵州茅台", kept[0][0])


if __name__ == "__main__":
    unittest.main()
