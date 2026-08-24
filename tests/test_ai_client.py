import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_client
from ai_client import AIError, chat_complete, chat_stream


class FakeResp:
    def __init__(self, status_code=200, lines=None):
        self.status_code = status_code
        self.lines = lines or []
        self.text = '{"error":{"message":"invalid"}}'

    def iter_lines(self, decode_unicode=True):
        return iter(self.lines)


class PrematureResp(FakeResp):
    """先产出部分 SSE 行，然后模拟连接被掐断。"""

    def __init__(self, lines_before_raise=None):
        super().__init__(lines=[])
        self._before = list(lines_before_raise or [])

    def iter_lines(self, decode_unicode=True):
        for ln in self._before:
            yield ln
        raise ai_client.requests.exceptions.ChunkedEncodingError("ended prematurely")


OPENAI_SSE = [
    'data: {"choices":[{"delta":{"reasoning":"想"}}]}',
    'data: {"choices":[{"delta":{"reasoning_content":"考"}}]}',
    '',
    'data: {"choices":[{"delta":{"content":"你好"}}]}',
    'data: {"choices":[{"delta":{"content":"！"},"finish_reason":null}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
    'data: [DONE]',
]
OPENAI_EXPECTED = [("reasoning", "想"), ("reasoning", "考"),
                   ("content", "你好"), ("content", "！")]

ANTHROPIC_SSE = [
    'event: message_start',
    'data: {"type":"message_start"}',
    'event: content_block_delta',
    'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"Think"}}',
    'event: content_block_delta',
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}',
    'event: message_stop',
    'data: {"type":"message_stop"}',
]
ANTHROPIC_EXPECTED = [("reasoning", "Think"), ("content", "Hi")]

MSGS = [{"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"}]


class TestChatStream(unittest.TestCase):
    def _patch_post(self, resp_or_seq):
        captured = {}
        seq = ([resp_or_seq] if isinstance(resp_or_seq, FakeResp)
               else list(resp_or_seq))

        def fake_post(url, headers=None, json=None, stream=False, timeout=None):
            captured.setdefault("calls", []).append(
                {"url": url, "headers": headers or {}, "payload": json or {}})
            captured.update(url=url, headers=headers or {}, payload=json or {})
            return seq.pop(0)

        cm = patch.object(ai_client.requests, "post", side_effect=fake_post)
        cm.start()
        self.addCleanup(cm.stop)
        return captured

    @staticmethod
    def _texts(events):
        return "".join(t for k, t in events if k == "content")

    @staticmethod
    def _events(events):
        return [(k, t) for k, t in events]

    def test_openai_stream_collects_content_and_reasoning(self):
        cap = self._patch_post(FakeResp(lines=OPENAI_SSE))
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        events = list(chat_stream(cfg, "m1", "sk-test", MSGS))
        self.assertEqual(self._events(events), OPENAI_EXPECTED)
        self.assertEqual(cap["url"], "https://x.example/v1/chat/completions")
        self.assertEqual(cap["headers"]["Authorization"], "Bearer sk-test")
        self.assertTrue(cap["payload"]["stream"])
        roles = [m["role"] for m in cap["payload"]["messages"]]
        self.assertEqual(roles, ["system", "user"])

    def test_anthropic_routes_to_messages(self):
        cap = self._patch_post(FakeResp(lines=ANTHROPIC_SSE))
        cfg = {"protocol": "anthropic", "base_url": "https://api.anthropic.com"}
        events = list(chat_stream(cfg, "claude-x", "ak-1", MSGS))
        self.assertEqual(self._events(events), ANTHROPIC_EXPECTED)
        self.assertEqual(cap["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(cap["headers"]["x-api-key"], "ak-1")
        self.assertEqual(cap["payload"]["system"], "sys")
        self.assertEqual([m["role"] for m in cap["payload"]["messages"]], ["user"])
        self.assertIn("max_tokens", cap["payload"])

    def test_http_401_raises_friendly_error(self):
        self._patch_post(FakeResp(status_code=401))
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        with self.assertRaises(AIError) as ctx:
            list(chat_stream(cfg, "m1", "bad", MSGS))
        self.assertIn("Key", str(ctx.exception))

    def test_network_error_retries_then_fails(self):
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            raise ai_client.requests.Timeout("timeout")

        cm = patch.object(ai_client.requests, "post", side_effect=flaky)
        cm.start()
        self.addCleanup(cm.stop)
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        with self.assertRaises(AIError) as ctx:
            list(chat_stream(cfg, "m1", "k", MSGS))
        self.assertEqual(calls["n"], 2)
        self.assertIn("网络连接失败", str(ctx.exception))

    def test_premature_disconnect_before_output_retries_once(self):
        good = FakeResp(lines=[
            'data: {"choices":[{"delta":{"content":"OK"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: [DONE]',
        ])
        cap = self._patch_post([PrematureResp([]), good])
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        events = list(chat_stream(cfg, "m1", "k", MSGS))
        self.assertEqual(self._texts(events), "OK")
        self.assertEqual(len(cap["calls"]), 2)

    def test_premature_disconnect_after_partial_output_raises(self):
        partial = [PrematureResp([
            'data: {"choices":[{"delta":{"reasoning":"半截"}}]}',
        ])]
        self._patch_post(partial + [
            FakeResp(lines=['data: {"choices":[{"delta":{"content":"OK"}}]}',
                            'data: [DONE]'])])
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        with self.assertRaises(AIError) as ctx:
            list(chat_stream(cfg, "m1", "k", MSGS))
        self.assertIn("流式连接中断", str(ctx.exception))

    def test_midstream_error_event(self):
        lines = ['data: {"error":{"message":"boom"}}']
        self._patch_post(FakeResp(lines=lines))
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        with self.assertRaises(AIError) as ctx:
            list(chat_stream(cfg, "m1", "k", MSGS))
        self.assertIn("boom", str(ctx.exception))

    def test_length_truncation_without_content_raises(self):
        lines = [
            'data: {"choices":[{"delta":{"reasoning":"长思考"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
            'data: [DONE]',
        ]
        self._patch_post(FakeResp(lines=lines))
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        with self.assertRaises(AIError) as ctx:
            list(chat_stream(cfg, "m1", "k", MSGS))
        self.assertIn("max_tokens", str(ctx.exception))


class TestChatComplete(unittest.TestCase):
    def _patch_post(self, resps):
        captured = {}
        seq = list(resps)

        def fake_post(url, headers=None, json=None, stream=False, timeout=None):
            captured.setdefault("calls", []).append(
                {"url": url, "headers": headers or {}, "payload": json or {}})
            return seq.pop(0)

        cm = patch.object(ai_client.requests, "post", side_effect=fake_post)
        cm.start()
        self.addCleanup(cm.stop)
        return captured

    @staticmethod
    def _texts(events):
        return "".join(t for k, t in events if k == "content")

    def test_zero_content_truncation_doubles_budget_and_retries(self):
        truncated = FakeResp(lines=[
            'data: {"choices":[{"delta":{"reasoning":"只想不答"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
            'data: [DONE]',
        ])
        good = FakeResp(lines=[
            'data: {"choices":[{"delta":{"content":"完整正文"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: [DONE]',
        ])
        cap = self._patch_post([truncated, good])
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        events = list(chat_complete(cfg, "m1", "k", MSGS, max_tokens=100))
        self.assertEqual(self._texts(events), "完整正文")
        restarts = [t for k, t in events if k == "restart"]
        self.assertEqual(len(restarts), 1)
        self.assertIn("200", restarts[0])
        self.assertIn("精简", restarts[0])
        self.assertEqual(cap["calls"][0]["payload"]["max_tokens"], 100)
        self.assertEqual(cap["calls"][1]["payload"]["max_tokens"], 200)
        # 重试请求应携带"精简推理"引导消息（省token），且不污染调用方列表
        retry_msgs = cap["calls"][1]["payload"]["messages"]
        self.assertEqual(retry_msgs[-1]["role"], "user")
        self.assertIn("精简", retry_msgs[-1]["content"])
        self.assertEqual(len(MSGS), 2)

    def test_partial_answer_auto_continues(self):
        part1 = FakeResp(lines=[
            'data: {"choices":[{"delta":{"reasoning":"思考"}}]}',
            'data: {"choices":[{"delta":{"content":"部分"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
            'data: [DONE]',
        ])
        part2 = FakeResp(lines=[
            'data: {"choices":[{"delta":{"content":"剩余"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: [DONE]',
        ])
        cap = self._patch_post([part1, part2])
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        events = list(chat_complete(cfg, "m1", "k", MSGS, max_tokens=100))
        self.assertEqual(self._texts(events), "部分剩余")
        notices = [t for k, t in events if k == "notice"]
        self.assertTrue(any("续写" in n for n in notices))
        second = cap["calls"][1]["payload"]["messages"]
        self.assertEqual(second[-1]["role"], "user")
        self.assertIn("继续", second[-1]["content"])

    def test_gives_up_at_budget_ceiling(self):
        def endless_truncation():
            return FakeResp(lines=[
                'data: {"choices":[{"delta":{"reasoning":"x"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
                'data: [DONE]',
            ])
        self._patch_post([endless_truncation() for _ in range(15)])
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        with self.assertRaises(AIError) as ctx:
            list(chat_complete(cfg, "m1", "k", MSGS, max_tokens=1024))
        self.assertIn("上限", str(ctx.exception))

    def test_config_validation_errors(self):
        cfg = {"protocol": "openai", "base_url": ""}
        with self.assertRaises(AIError):
            list(chat_stream(cfg, "m", "k", MSGS))
        cfg = {"protocol": "openai", "base_url": "https://x/v1"}
        with self.assertRaises(AIError):
            list(chat_stream(cfg, "", "k", MSGS))
        with self.assertRaises(AIError):
            list(chat_stream(cfg, "m", "", MSGS))

    def test_extra_params_merged_into_openai_payload(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: [DONE]',
        ]
        cap = self._patch_post([FakeResp(lines=lines)])
        cfg = {"protocol": "openai", "base_url": "https://x.example/v1"}
        extra = {"thinking": {"type": "disabled"}, "reasoning_effort": "low"}
        list(chat_stream(cfg, "m1", "k", MSGS, extra_params=extra))
        self.assertEqual(cap["calls"][0]["payload"]["thinking"],
                         {"type": "disabled"})
        self.assertEqual(cap["calls"][0]["payload"]["reasoning_effort"], "low")

    def test_openrouter_preset(self):
        cfg = ai_client.PROVIDERS["openrouter"]
        self.assertEqual(cfg["protocol"], "openai")
        self.assertEqual(cfg["base_url"], "https://openrouter.ai/api/v1")
        self.assertIn("stealth/ox-alpha", cfg["models"])


class TestLocalConfig(unittest.TestCase):
    def test_roundtrip_and_sanitize(self):
        with tempfile.TemporaryDirectory() as td:
            old = ai_client.CONFIG_PATH
            ai_client.CONFIG_PATH = Path(td) / ".streamlit" / "secrets.toml"
            try:
                self.assertEqual(ai_client.load_local_config(), {})
                ai_client.save_local_config({"provider": "deepseek",
                                             "base_url": "https://a/v1",
                                             "model": "m\"x",
                                             "api_key": "sk-1"})
                loaded = ai_client.load_local_config()
                self.assertEqual(loaded["provider"], "deepseek")
                self.assertEqual(loaded["model"], "m'x")
                self.assertEqual(loaded["api_key"], "sk-1")
                self.assertTrue(ai_client.clear_local_config())
                self.assertEqual(ai_client.load_local_config(), {})
                self.assertFalse(ai_client.clear_local_config())
            finally:
                ai_client.CONFIG_PATH = old


if __name__ == "__main__":
    unittest.main()
