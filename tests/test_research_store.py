import os
import tempfile
import unittest
from types import SimpleNamespace

import research_store as store


class TestResearchStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("ASTOCK_DB_PATH")
        os.environ["ASTOCK_DB_PATH"] = os.path.join(self.tmp.name, "research.db")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("ASTOCK_DB_PATH", None)
        else:
            os.environ["ASTOCK_DB_PATH"] = self.old
        self.tmp.cleanup()

    def test_watchlist_thesis_snapshot_and_evidence_roundtrip(self):
        store.add_watchlist("600519", "贵州茅台")
        store.save_thesis(
            "600519", "贵州茅台", thesis="渠道库存下降", catalysts="旺季动销",
            risks="需求转弱", invalidation="批价连续跌破阈值", conviction=4,
            next_review="2026-10-01")
        store.save_snapshot("600519", "贵州茅台", price=1500.0, chg_pct=1.2,
                            report_text="规则报告", payload={"period": "daily"})
        item = SimpleNamespace(
            title="公司公告", url="https://example.com/a?utm_source=x", snippet="摘要",
            source_name="交易所", channel="cninfo", published="2026-09-26",
            quality_score=110, stance="neutral", impact="mid",
            verified="official", reason="正式披露")
        store.save_news_items("600519", [item, item])

        self.assertEqual(store.overview()["watchlist"], 1)
        self.assertEqual(store.latest_thesis("600519")["conviction"], 4)
        self.assertIn("批价连续跌破阈值", store.thesis_context("600519"))
        self.assertEqual(len(store.list_snapshots("600519")), 1)
        self.assertEqual(len(store.list_evidence("600519")), 1)
        self.assertIn("失效条件", store.export_dossier("600519"))

    def test_news_refresh_preserves_fulltext_hash_and_health_recovers(self):
        item = SimpleNamespace(
            title="正式公告", url="https://example.com/a", snippet="初始摘要",
            source_name="交易所", channel="cninfo", published="2026-09-26",
            quality_score=110, stance="neutral", impact="mid",
            verified="official", reason="正式披露")
        store.save_news_items("600519", [item])
        evidence = store.list_evidence("600519")[0]
        store.update_evidence_content(evidence["evidence_id"], full_text="公告完整正文")
        full_hash = store.list_evidence("600519")[0]["content_hash"]
        item.snippet = "刷新后的摘要"
        store.save_news_items("600519", [item])
        refreshed = store.list_evidence("600519")[0]
        self.assertEqual(refreshed["content_hash"], full_hash)
        self.assertEqual(refreshed["full_text"], "公告完整正文")

        store.save_source_health("brave", False, 0, "HTTP 429")
        self.assertTrue(store.list_source_health()[0]["rate_limited_until"])
        store.save_source_health("brave", True, 3)
        self.assertEqual(store.list_source_health()[0]["rate_limited_until"], "")


if __name__ == "__main__":
    unittest.main()
