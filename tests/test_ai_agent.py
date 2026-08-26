import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_agent
from ai_agent import MAX_TOOL_ROUNDS, run_agent
from ai_client import AIError

CFG = {"protocol": "openai", "base_url": "https://x.example/v1"}
MSGS = [{"role": "system", "content": "sys"},
        {"role": "user", "content": "天洋新材今天有什么新闻？"}]

FAKE_SEARCH_RESULT = "[1]《半年报》净利润扭亏(https://example.com/a)"


def _tool_call_msg(query="天洋新材 财报"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "web_search",
                                         "arguments": json.dumps({"query": query})}}],
            "finish_reason": "tool_calls"}


def _content_msg(text="结论 [1]"):
    return {"role": "assistant", "content": text, "tool_calls": [],
            "finish_reason": "stop"}


class TestRunAgent(unittest.TestCase):
    def setUp(self):
        self._saved = (ai_agent.chat_once, ai_agent.execute_tool)
        self.executed = []

    def tearDown(self):
        ai_agent.chat_once, ai_agent.execute_tool = self._saved

    def _install(self, responses, tool_result=FAKE_SEARCH_RESULT):
        calls = []
        seq = list(responses)

        def fake_chat_once(cfg, model, key, messages, **kw):
            calls.append({"model": model, "messages": [dict(m) for m in messages],
                          "tools": kw.get("tools"),
                          "max_tokens": kw.get("max_tokens")})
            return dict(seq.pop(0))

        def fake_exec(name, args):
            self.executed.append((name, args))
            if name == "web_search":
                return FAKE_SEARCH_RESULT
            return f"exec:{name}"

        p1 = patch.object(ai_agent, "chat_once", side_effect=fake_chat_once)
        p2 = patch.object(ai_agent, "execute_tool", side_effect=fake_exec)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        return calls

    def test_tool_round_then_answer_with_sources(self):
        calls = self._install([_tool_call_msg(), _content_msg("结论 [1]")])
        events = list(run_agent(CFG, "m1", "k", MSGS, mode="on"))
        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "status")
        self.assertIn(kinds[-2:], (["sources", "answer"],))
        answer = dict(events)["answer"]
        self.assertEqual(answer, "结论 [1]")
        sources = [v for k, v in events if k == "sources"][0]
        self.assertEqual(sources, [("半年报", "https://example.com/a")])
        # 首轮带工具，第二轮消息含 tool 角色结果
        self.assertTrue(calls[0]["tools"])
        last = calls[1]["messages"][-1]
        self.assertEqual(last["role"], "tool")
        self.assertIn("净利润扭亏", last["content"])
        self.assertEqual(self.executed, [("web_search",
                                          {"query": "天洋新材 财报"})])

    def test_max_rounds_exceeded_raises(self):
        self._install([_tool_call_msg() for _ in range(MAX_TOOL_ROUNDS + 2)])
        with self.assertRaises(AIError) as ctx:
            list(run_agent(CFG, "m1", "k", MSGS, mode="on"))
        self.assertIn("最大工具轮次", str(ctx.exception))

    def test_native_mode_appends_suffix_and_no_tools(self):
        calls = self._install([_content_msg("原生回答")])
        list(run_agent({"protocol": "openai"}, "m1", "k", MSGS, mode="native"))
        self.assertEqual(calls[0]["model"], "m1:online")
        self.assertIsNone(calls[0]["tools"])

    def test_budget_doubles_on_length_truncation(self):
        truncated = _content_msg("")
        truncated["finish_reason"] = "length"
        calls = self._install([truncated, _content_msg("OK")])
        list(run_agent(CFG, "m1", "k", MSGS, budget=100))
        self.assertEqual(calls[0]["max_tokens"], 100)
        self.assertEqual(calls[1]["max_tokens"], 200)

    def test_anthropic_protocol_degrades_gracefully(self):
        cfg = {"protocol": "anthropic", "base_url": "https://a.example"}
        calls = self._install([_content_msg("普通回答")])
        events = list(run_agent(cfg, "claude-x", "k", MSGS, mode="on"))
        self.assertIsNone(calls[0]["tools"])          # 工具被忽略
        statuses = [t for k, t in events if k == "status"]
        self.assertTrue(any("不支持" in s for s in statuses))


class TestReadPage(unittest.TestCase):
    def test_read_page_strips_html(self):
        class FakeResp:
            text = ("<html><head><style>.x{}</style></head><body>"
                    "<script>var a=1;</script>"
                    "<p>正文关键词ABC，这里是一段足够长的示例正文内容，"
                    "用于验证HTML标签剥离与空白压缩逻辑是否正常工作，"
                    "同时确保长度超过最小可提取阈值以便通过校验。</p></body></html>")
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def raise_for_status(self):
                pass

        with patch.object(ai_agent.__builtins__ and
                          __import__("search_tools").requests, "get",
                          return_value=FakeResp()):
            import search_tools as st_
            out = st_.tool_read_page("https://example.com/news")
        self.assertIn("正文关键词ABC", out)
        self.assertNotIn("<p>", out)
        self.assertNotIn("var a=1", out)

    def test_read_page_rejects_non_http(self):
        import search_tools as st_
        self.assertIn("仅支持 http(s)", st_.tool_read_page("ftp://x"))

    def test_execute_tool_unknown(self):
        import search_tools as st_
        self.assertIn("未知工具", st_.execute_tool("nope", {}))

    def test_web_search_empty_query(self):
        import search_tools as st_
        self.assertIn("query 为空", st_.tool_web_search(""))


if __name__ == "__main__":
    unittest.main()
