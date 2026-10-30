import json
import subprocess
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_reach_backend as backend
import ai_agent
import search_tools as tools
from search_workflow import SearchReport


class FixedDate(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 7, 12, tzinfo=timezone.utc)


class TestAgentReachBackend(unittest.TestCase):
    def test_text_results_preserve_date_source_and_multiline_highlights(self):
        output = (
            "Title: 宁德时代公告\nURL: https://www.catl.com/news/1.html\n"
            "Published: 2026-10-06T00:00:00Z\nAuthor: 公司\nHighlights:\n"
            "签署储能订单。\n订单仍需履约。\n\n---\n\n"
            "Title: 无日期线索\nURL: https://example.com/a\nText: 需进一步核验\n")
        rows = backend.parse_search_output(output)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][3], "2026-10-06T00:00:00Z")
        self.assertIn("订单仍需履约", rows[0][2])
        self.assertEqual(rows[0][4], backend.ENGINE)
        self.assertEqual(rows[1][3], "")

    def test_json_and_mcp_envelopes_and_invalid_urls(self):
        results = {"results": [
            {"title": "公告", "url": "https://example.com/a", "highlights": ["事实1", "事实2"],
             "publishedDate": "2026-10-06"},
            {"title": "无效", "url": "javascript:alert(1)"}]}
        expected = backend.parse_search_output(json.dumps(results))
        self.assertEqual(len(expected), 1)
        self.assertEqual(expected[0][2], "事实1 事实2")
        for envelope in ({"structuredContent": results}, {"content": [
                {"type": "text", "text": json.dumps(results)}]}):
            self.assertEqual(backend.parse_search_output(json.dumps(envelope)), expected)
        with self.assertRaisesRegex(RuntimeError, "exa_tool_error"):
            backend.parse_search_output('{"isError": true, "content": []}')
        with self.assertRaisesRegex(RuntimeError, "exa_unrecognized_output"):
            backend.parse_search_output("service unavailable")

    @patch.object(backend, "datetime", FixedDate)
    @patch.object(backend, "mcporter_command", return_value=["mcporter"])
    @patch.object(backend.subprocess, "run")
    def test_search_filters_stale_and_future_dates_and_uses_safe_arguments(self, run, command):
        run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({"results": [
            {"title": "近期", "url": "https://example.com/new", "publishedDate": "2026-10-06"},
            {"title": "旧闻", "url": "https://example.com/old", "publishedDate": "2025-01-01"},
            {"title": "未来", "url": "https://example.com/future", "publishedDate": "2027-01-01"},
            {"title": "待核验", "url": "https://example.com/unknown"}]}))
        query = '宁德时代 "订单"; $(secret) `command`'
        rows = backend.search(query, "w")
        self.assertEqual([r[0] for r in rows], ["近期", "待核验"])
        args, kwargs = run.call_args
        argv = args[0]
        payload = json.loads(argv[argv.index("--args") + 1])
        self.assertEqual(payload["query"], query)
        self.assertIn("2026-09-30", payload["objective"])
        self.assertEqual(argv[argv.index("--config") + 1], str(backend.CONFIG))
        self.assertFalse(kwargs.get("shell", False))
        self.assertLessEqual(kwargs["timeout"], 25)

    @patch.object(backend, "mcporter_command", return_value=["mcporter"])
    @patch.object(backend.subprocess, "run")
    @patch.object(backend, "datetime", FixedDate)
    def test_unrestricted_and_exact_day_search_windows(self, run, command):
        run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({"results": [
            {"title": "历史公告", "url": "https://example.com/a", "publishedDate": "2026-05-01"}]}))
        self.assertEqual(len(backend.search("历史公告", "")), 1)
        self.assertEqual(len(backend.search("半年公告", "d180")), 1)
        self.assertEqual(backend.search("近3天公告", "d3"), [])

    @patch.object(backend, "mcporter_command", return_value=["mcporter"])
    @patch.object(backend.subprocess, "run")
    def test_timeout_and_nonzero_exit_do_not_expose_process_output(self, run, command):
        run.side_effect = subprocess.TimeoutExpired("mcporter", 20)
        with self.assertRaisesRegex(RuntimeError, "exa_timeout"):
            backend.search("公告", "w")
        run.side_effect = None
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="private-token")
        with self.assertRaisesRegex(RuntimeError, "^exa_command_failed$"):
            backend.search("公告", "w")

    def test_reader_extracts_markdown_and_rejects_target_error(self):
        response = Mock(text="Title: 示例\nURL Source: https://example.com\nMarkdown Content:\n" + "正文内容。" * 20)
        get = Mock(return_value=response)
        self.assertEqual(backend.read_page("https://example.com", get), "正文内容。" * 20)
        self.assertEqual(get.call_args.args[0], "https://r.jina.ai/https://example.com")
        response.text = "Warning: Target URL returned error 403: Forbidden\n" + "无内容" * 20
        with self.assertRaisesRegex(RuntimeError, "jina_empty_or_blocked"):
            backend.read_page("https://example.com", get)


class TestSearchRouting(unittest.TestCase):
    def setUp(self):
        self.health = patch.object(tools, "_record_health").start()
        self.addCleanup(patch.stopall)

    def test_exa_success_filters_domains_deduplicates_and_skips_fallback(self):
        rows = [
            ("宁德时代公告", "https://www.catl.com/news/1?utm_source=a", "订单", "2026-10-06", backend.ENGINE),
            ("宁德时代公告", "https://www.catl.com/news/1", "订单", "2026-10-06", backend.ENGINE),
            ("宁德时代公告", "https://other.com/news/1", "订单", "2026-10-06", backend.ENGINE)]
        with patch.object(backend, "search", return_value=rows), patch.object(tools, "_via_ddg") as ddg:
            result = tools.search_raw("宁德时代 site:catl.com", "week")
        self.assertEqual(len(result), 1)
        ddg.assert_not_called()
        self.assertEqual(self.health.call_args.args[:3], (backend.ENGINE, True, 1))

    def test_exa_failure_or_irrelevant_results_fall_back(self):
        for result in (RuntimeError("exa_timeout"), [], [
                ("其他公司", "https://example.com/other", "其他公司公告", "", backend.ENGINE)]):
            exa = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with patch.object(backend, "search", **exa), \
                    patch.object(tools, "_via_brave", return_value=[]), \
                    patch.object(tools, "_via_tavily", return_value=[]), \
                    patch.object(tools, "_via_google_cse", return_value=[]), \
                    patch.object(tools, "_via_ddg", return_value=[
                        ("宁德时代公告", "https://example.com/catl", "宁德时代")]) as ddg, \
                    patch.object(tools, "_via_google_news", return_value=[]), \
                    patch.object(tools, "_via_bing_rss", return_value=[]):
                rows = tools.search_raw("宁德时代", "week")
            self.assertEqual(rows[0][0], "宁德时代公告")
            ddg.assert_called_once()

    def test_ai_tool_preserves_source_links_and_engine(self):
        with patch.object(backend, "search", return_value=[
                ("宁德时代公告", "https://example.com/catl", "订单事实", "2026-10-06", backend.ENGINE)]), \
                patch("research_store.search_evidence", return_value=[]), \
                patch.object(tools, "tool_read_page", return_value="读取失败：离线测试"):
            text = tools.execute_tool("web_search", {"query": "宁德时代"}, research_config={})
        self.assertIn(backend.ENGINE, text)
        self.assertIn("2026-10-06", text)
        self.assertEqual(tools.extract_sources(text), [("宁德时代公告", "https://example.com/catl")])

    def test_agent_returns_research_sources_to_ui(self):
        report = SearchReport("查公告", mode="agent", status="complete", stop_reason="evidence_sufficient",
                              sources=[{"source_id": "W1", "title": "宁德时代公告",
                                        "url": "https://example.com/catl", "published": "",
                                        "engine": "agent-reach-exa", "read_status": "read",
                                        "snippet": "订单", "excerpt": "原文订单"}])
        with patch.object(ai_agent, "chat_once", return_value={"content": "基于公告的回答 [W1]"}) as chat:
            events = list(ai_agent.run_agent({"protocol": "openai"}, "model", "key", [
                {"role": "system", "content": "研究规则"}, {"role": "user", "content": "查公告"}],
                research_fn=lambda *a, **kw: report))
        self.assertIn(("sources", [("[W1] 宁德时代公告", "https://example.com/catl")]), events)
        self.assertIn("Agent-Reach", chat.call_args.args[3][0]["content"])
        self.assertEqual(events[-1], ("answer", "基于公告的回答 [W1]"))

    def test_jina_success_and_failure_fall_back_to_direct_page(self):
        with patch.object(backend, "read_page", return_value="正文内容。" * 20), \
                patch.object(tools, "_engine_get") as direct:
            text = tools.tool_read_page("https://example.com")
            direct.assert_not_called()
            self.assertIn("Jina Reader", text)
        response = Mock(headers={"Content-Type": "text/html"}, text="<p>" + "直连正文。" * 20 + "</p>")
        with patch.object(backend, "read_page", side_effect=RuntimeError("blocked")), \
                patch.object(tools, "_engine_get", return_value=response):
            self.assertIn("直连正文", tools.tool_read_page("https://example.com"))

    def test_pdf_keeps_page_numbers_and_bypasses_jina(self):
        response = Mock(headers={"Content-Type": "application/pdf"}, content=b"%PDF-1.4")
        with patch.object(backend, "read_page") as jina, \
                patch.object(tools, "_engine_get", return_value=response), \
                patch.object(tools, "_extract_pdf_text", return_value="[PDF第1页]\n公告正文"):
            self.assertIn("[PDF第1页]", tools.tool_read_page("https://example.com/a.pdf?download=1"))
            jina.assert_not_called()


if __name__ == "__main__":
    unittest.main()
