import unittest

from news_intel import NewsItem
from news_quality import evidence_text, rank_and_dedup


class TestNewsQuality(unittest.TestCase):
    def test_authoritative_source_ranks_before_social(self):
        social = NewsItem(channel="x", title="公司发布季度业绩", published="2026-09-25")
        wire = NewsItem(channel="reuters", title="公司确认季度业绩增长", published="2026-09-25")
        ranked = rank_and_dedup([social, wire], now=__import__("datetime").datetime(2026, 9, 25))
        self.assertEqual(ranked[0].channel, "reuters")
        self.assertGreater(ranked[0].quality_score, social.quality_score)
        self.assertIn("source_authority", ranked[0].quality_breakdown)
        self.assertEqual(ranked[0].quality_breakdown["total"], ranked[0].quality_score)

    def test_generic_market_landing_page_is_removed(self):
        generic = NewsItem(channel="bloomberg", title="Markets", url="https://example.com/markets")
        real = NewsItem(channel="bloomberg", title="Federal Reserve cuts rates after meeting")
        ranked = rank_and_dedup([generic, real])
        self.assertEqual([item.title for item in ranked], [real.title])

    def test_duplicate_keeps_higher_quality_and_evidence_ids(self):
        repost = NewsItem(channel="x", title="贵州茅台发布年度业绩公告")
        official = NewsItem(channel="cninfo", title="贵州茅台发布年度业绩公告",
                            url="https://example.com/notice")
        ranked = rank_and_dedup([repost, official], name="贵州茅台")
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].channel, "cninfo")
        block, sources = evidence_text(ranked)
        self.assertIn("[N1]", block)
        self.assertEqual(sources[0][1], "https://example.com/notice")


if __name__ == "__main__":
    unittest.main()
