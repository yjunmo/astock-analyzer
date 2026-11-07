import os
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

import pandas as pd

from astock.monitor import monitor_service
from astock.research import research_analytics
from astock.research import research_services as services
from astock.research import research_store as store


class TestResearchSystem(unittest.TestCase):
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

    @staticmethod
    def item(title="公司回购公告", url="https://www.cninfo.com.cn/a"):
        return SimpleNamespace(
            title=title, url=url, snippet="董事会审议通过回购方案", source_name="巨潮资讯",
            channel="cninfo", published="2026-09-20", quality_score=110,
            stance="bull", impact="high", verified="official", reason="正式披露",
            sectors=["消费"])

    def test_full_research_loop_persists_relationships(self):
        project_id = services.create_project("回购研究", "回购是否改善每股收益", "事件研究")
        services.add_project_stock(project_id, "600519", "贵州茅台", "core")
        thesis_id = store.save_thesis(
            "600519", "贵州茅台", thesis="回购提高股东回报", catalysts="回购实施",
            risks="消费需求走弱", invalidation="终止回购", conviction=4,
            next_review="2026-10-01", validation_metrics="回购进度",
            valid_until="2026-12-31", project_id=project_id)
        result = services.process_news_intelligence("600519", "贵州茅台", [self.item()])
        self.assertEqual(result["events"], 1)
        evidence = store.list_evidence("600519")
        services.link_thesis_evidence(thesis_id, [evidence[0]["evidence_id"]], "support")
        self.assertEqual(services.thesis_evidence(thesis_id)[0]["stance"], "support")
        nodes, edges = services.graph_data("600519")
        self.assertTrue(nodes)
        self.assertTrue(edges)
        self.assertEqual(len(services.list_events("600519")), 1)

    def test_structured_ai_archive_marks_complete_and_incomplete(self):
        complete = """## 核心结论\n结论方向：看多 [T1]\n## 支持事实\n支持\n## 反对事实\n反对\n## 失效条件\n跌破阈值\n## 下一跟踪指标\n订单\n## 乐观情景\n增长\n## 基准情景\n平稳\n## 悲观情景\n下滑\n## 待确认问题\n无"""
        run_id = services.archive_research_run("300750", "宁德时代", "研究", complete,
                                               model="deepseek-flash", baseline_price=200,
                                               evidence_registry={"T1": {"available": True}})
        self.assertEqual(services.list_research_runs("300750")[0]["status"], "complete")
        self.assertIn("research_due",
                      {r["rule_type"] for r in monitor_service.list_rules("300750")})
        self.assertGreaterEqual(len(services.build_research_plan("研究", "panorama")), 5)
        services.review_run(run_id, "partial", "方向正确，幅度偏差")
        self.assertEqual(services.list_research_runs("300750")[0]["outcome"], "partial")
        services.archive_research_run("300750", "宁德时代", "研究", "只有一句结论")
        self.assertEqual(services.list_research_runs("300750")[0]["status"], "incomplete")

    def test_research_task_stages_are_persisted(self):
        task_id = services.start_research_task("300750", "宁德时代", "储能订单是否兑现", "panorama")
        services.update_research_task(task_id, "cross_validate", evidence_count=6,
                                      coverage=75, conflict_count=1,
                                      unresolved=["海外订单口径"])
        task = services.list_research_tasks("300750")[0]
        self.assertEqual(task["current_stage"], "cross_validate")
        self.assertEqual(task["evidence_count"], 6)
        self.assertIn("海外订单口径", task["unresolved_json"])
        sub_status = json.loads(task["subquestion_status_json"])
        self.assertTrue(any(x["status"] == "complete" for x in sub_status))
        self.assertTrue(any(x["status"] == "running" for x in sub_status))

    def test_research_task_subquestions_require_review_when_archive_is_incomplete(self):
        task_id = services.start_research_task("300750", "宁德时代", "研究订单", "custom")
        services.archive_research_run("300750", "宁德时代", "研究订单", "只有一句结论",
                                      task_id=task_id)
        task = services.list_research_tasks("300750")[0]
        self.assertEqual(task["status"], "incomplete")
        statuses = {x["status"] for x in json.loads(task["subquestion_status_json"])}
        self.assertEqual(statuses, {"needs_review"})
        self.assertEqual(json.loads(task["stage_json"])["archive"], "needs_review")

    def test_semantic_event_cluster_and_numeric_conflict(self):
        first = self.item("宁德时代获得100亿元储能订单", "https://a.example/order")
        first.source_name = "媒体A"; first.channel = "cls"; first.verified = "unknown"
        first.snippet = "订单金额100亿元"
        follow = self.item("宁德时代储能订单最新进展", "https://b.example/order")
        follow.source_name = "媒体B"; follow.channel = "stcn"; follow.verified = "unknown"
        follow.published = "2026-09-21"; follow.snippet = "报道金额120亿元"
        services.process_news_intelligence("300750", "宁德时代", [first, follow])
        events = services.list_events("300750")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_count"], 2)
        self.assertIn("数字不一致", events[0]["contradiction"])
        kinds = {x["item_kind"] for x in store.list_evidence("300750")}
        self.assertIn("followup", kinds)

    def test_financial_report_period_becomes_timeline_event(self):
        snapshot = {"ok": True, "period": "2026-06-30",
                    "trend": {"report_periods": ["2026-03-31", "2026-06-30"],
                              "revenue": [10, 20], "profit": [1, 2]},
                    "profit": {"roe": 12}, "growth": {}, "solvency": {}, "cashflow": {}}
        self.assertEqual(research_analytics.ingest_financial_report_events(
            "300750", "宁德时代", snapshot), 2)
        periods = {x["earliest_at"] for x in services.list_events("300750")}
        self.assertEqual(periods, {"2026-03-31", "2026-06-30"})

    def test_fts_alias_alert_dedup_and_portfolio(self):
        item = self.item("重大订单公告", "https://www.cninfo.com.cn/order")
        store.save_news_items("300750", [item])
        self.assertEqual(store.search_evidence("订单", "300750")[0]["title"], "重大订单公告")
        store.upsert_entity_alias("300750", "CATL Europe", "subsidiary", True)
        self.assertEqual(store.list_entity_aliases("300750")[0]["alias"], "CATL Europe")
        monitor_service.create_rule("300750", "宁德时代", "news_keyword", "订单")
        one = monitor_service.scan_rules(send_webhook=False, force=True)
        two = monitor_service.scan_rules(send_webhook=False, force=True)
        self.assertEqual(one["triggered"], 1)
        self.assertEqual(two["triggered"], 0)
        self.assertEqual(len(monitor_service.list_notifications()), 1)

        pid = services.create_portfolio("核心组合")
        services.upsert_position(pid, "300750", "宁德时代", 100, 180, "新能源")
        positions = services.list_positions(pid)
        dates = pd.date_range("2026-01-01", periods=130, freq="B")
        history = pd.DataFrame({"date": dates, "close": range(100, 230)})
        risk = research_analytics.analyze_portfolio(
            positions, {"300750": history}, market_caps={"300750": 1_000_000})
        self.assertAlmostEqual(risk["rows"][0]["weight"], 100)
        self.assertIn("单一持仓权重超过30%", risk["warnings"])
        self.assertEqual(risk["style_exposure"]["large_cap_weight"], 100)

    def test_event_study_uses_only_forward_trading_rows(self):
        dates = pd.date_range("2026-01-01", periods=80, freq="B")
        stock = pd.DataFrame({"date": dates, "close": [100 + i for i in range(80)]})
        bench = pd.DataFrame({"date": dates, "close": [200 + i * .5 for i in range(80)]})
        event_date = dates[10].date().isoformat()
        out = research_analytics.compute_event_study(stock, event_date, bench)
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(out["returns"]["1"], (111 / 110 - 1) * 100, places=4)
        self.assertIsNotNone(out["excess"]["60"])

    def test_legacy_database_migrates_idempotently_without_loss(self):
        path = os.environ["ASTOCK_DB_PATH"]
        conn = sqlite3.connect(path)
        conn.executescript("""
          CREATE TABLE watchlist(symbol TEXT PRIMARY KEY,name TEXT,tags TEXT,note TEXT,created_at TEXT,updated_at TEXT);
          CREATE TABLE theses(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,name TEXT,thesis TEXT,catalysts TEXT,risks TEXT,invalidation TEXT,conviction INTEGER,next_review TEXT,created_at TEXT);
          CREATE TABLE snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,name TEXT,price REAL,chg_pct REAL,report_text TEXT,payload_json TEXT,created_at TEXT);
          CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY,symbol TEXT,title TEXT,url TEXT,snippet TEXT,source TEXT,tier TEXT,published TEXT,quality_score INTEGER,payload_json TEXT,first_seen TEXT,last_seen TEXT);
          INSERT INTO watchlist VALUES('600519','贵州茅台','','','2026-01-01','2026-01-01');
        """)
        conn.commit(); conn.close()
        self.assertEqual(store.overview()["watchlist"], 1)
        self.assertEqual(store.overview()["watchlist"], 1)
        with store.connection() as migrated:
            self.assertEqual(migrated.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "3")


if __name__ == "__main__":
    unittest.main()
