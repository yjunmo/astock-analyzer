import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import search_tools
from scripts import agent_reach_twitter as gateway
import twitter_backend as backend


def tweet(ident="123", date="2026-10-06T12:00:00Z"):
    return {"id": ident, "text": "semiconductor supply chain 原始正文", "createdAtISO": date,
            "author": {"screenName": "Reuters"}}


class TestTwitterBackend(unittest.TestCase):
    def setUp(self):
        backend._CACHE.clear()
        self.health = patch.object(search_tools, "_record_health").start()
        self.addCleanup(patch.stopall)

    def test_only_explicit_twitter_site_queries_are_routed(self):
        self.assertEqual(backend.targeted_query("semiconductor site:x.com"), "semiconductor")
        self.assertEqual(backend.targeted_query("site:twitter.com from:Reuters oil"), "from:Reuters oil")
        for query in ["Twitter 最新消息", "新闻", "site:x.com.evil.com chip", "site:x.com chip site:news.cn"]:
            self.assertEqual(backend.targeted_query(query), "")

    def test_tweet_url_exact_host_and_path(self):
        self.assertEqual(backend.tweet_id("https://x.com/Reuters/status/123?s=1"), "123")
        self.assertEqual(backend.tweet_id("https://twitter.com/i/web/status/123"), "123")
        for url in ["https://x.com.evil.com/Reuters/status/123", "file:///Reuters/status/123", "https://x.com/Reuters", "https://x.com/Reuters/status/no"]:
            self.assertEqual(backend.tweet_id(url), "")

    @patch.object(backend, "_run")
    def test_native_results_use_cached_body_and_preserve_long_article(self, run):
        item = tweet(date="")
        item["articleText"] = "长文内容"
        run.return_value = {"data": [item]}
        rows = backend.search("semiconductor site:x.com", "w")
        self.assertEqual(len(rows), 1)
        self.assertIn("since:", run.call_args.args[1])
        self.assertEqual(rows[0][4], backend.ENGINE)
        body = backend.read_page(rows[0][1])
        self.assertIn("原始正文", body)
        self.assertIn("长文内容", body)
        run.assert_called_once()

    @patch.object(backend, "_run")
    def test_stale_future_invalid_results_are_excluded_unknown_date_retained(self, run):
        run.return_value = {"data": [tweet(date="2020-01-01T00:00:00Z"), tweet("124", "2100-01-01T00:00:00Z"), tweet("125", ""), {"id": "bad"}]}
        rows = backend.search("semiconductor site:x.com", "d3")
        self.assertEqual([backend.tweet_id(row[1]) for row in rows], ["125"])

    @patch.object(backend, "_run", return_value={"data": []})
    def test_missing_original_is_failure_not_reply_body(self, run):
        with self.assertRaisesRegex(RuntimeError, "twitter_original_unavailable"):
            backend.read_page("https://x.com/Reuters/status/123")

    @patch.object(backend, "_command", return_value=["python", "gateway"])
    @patch.object(backend.subprocess, "run")
    def test_bounded_subprocess_arguments_and_error_output_do_not_expose_secrets(self, run, command):
        run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": []}))
        query = 'chip; $(secret) `command`'
        backend._run("search", query, 500)
        self.assertEqual(run.call_args.args[0], ["python", "gateway", "search", query, "--max", "20"])
        self.assertEqual(run.call_args.kwargs["timeout"], 27)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        run.return_value = SimpleNamespace(returncode=1, stdout='{"ok": false, "error": "private-token"}', stderr="secret")
        with self.assertRaisesRegex(RuntimeError, "^twitter_command_failed$"):
            backend._run("search", "chip")
        run.side_effect = subprocess.TimeoutExpired("private-token", 27)
        with self.assertRaisesRegex(RuntimeError, "^twitter_timeout$"):
            backend._run("search", "chip")

    def test_native_and_exa_merge_with_native_original_preferred(self):
        native = [("semiconductor 原文", "https://x.com/Reuters/status/123", "semiconductor 完整正文", "", backend.ENGINE)]
        exa = [("semiconductor 摘要", native[0][1], "semiconductor 简短摘要", "", "agent-reach-exa"),
               ("semiconductor 其他", "https://x.com/other/status/124", "semiconductor", "", "agent-reach-exa")]
        with patch.object(backend, "search", return_value=native), patch.object(search_tools.agent_reach_backend, "search", return_value=exa):
            result = search_tools.search_raw("semiconductor site:x.com")
        self.assertEqual(len(result), 2)
        self.assertIn(native[0], result)

    def test_auth_failure_falls_back_to_exa(self):
        exa = [("semiconductor", "https://x.com/Reuters/status/123", "semiconductor", "", "agent-reach-exa")]
        with patch.object(backend, "search", side_effect=RuntimeError("twitter_not_authenticated")), patch.object(search_tools.agent_reach_backend, "search", return_value=exa):
            self.assertEqual(search_tools.search_raw("semiconductor site:x.com"), exa)
        self.assertIn(backend.ENGINE, [c.args[0] for c in self.health.call_args_list])

    def test_other_sites_do_not_invoke_native_backend(self):
        exa = [("公告", "https://news.cn/a", "新闻", "", "agent-reach-exa")]
        with patch.object(backend, "search") as native, patch.object(search_tools.agent_reach_backend, "search", return_value=exa):
            search_tools.search_raw("site:news.cn")
        native.assert_not_called()

    def test_tweet_reader_success_skips_web_reader(self):
        with patch.object(backend, "read_page", return_value="原始推文" * 50), patch.object(search_tools.agent_reach_backend, "read_page") as web:
            result = search_tools.tool_read_page("https://x.com/Reuters/status/123")
        self.assertIn("twitter-cli", result)
        web.assert_not_called()


class TestGatewaySafety(unittest.TestCase):
    def test_no_credentials_fail_before_client_or_browser_import(self):
        with patch.object(gateway, "credentials", return_value=("", "")):
            with self.assertRaisesRegex(RuntimeError, "^not_configured$"):
                gateway.fetch("search", "chip")

    def test_credentials_use_explicit_environment_without_mutation(self):
        cfg = {"twitter_auth_token": "saved-auth", "twitter_ct0": "saved-ct0"}
        with patch.dict(os.environ, {"TWITTER_AUTH_TOKEN": "explicit-auth"}, clear=True):
            self.assertEqual(gateway.credentials(cfg), ("explicit-auth", "saved-ct0"))
            self.assertNotIn("TWITTER_CT0", os.environ)

    def test_direct_read_only_client_never_uses_cookie_discovery(self):
        client = Mock()
        client.fetch_search.return_value = [SimpleNamespace(id="123")]
        client.fetch_tweet_detail.return_value = [SimpleNamespace(id="124"), SimpleNamespace(id="123")]
        factory = Mock(return_value=client)
        cookie_discovery = Mock(side_effect=AssertionError("must never read browser"))
        modules = {"twitter_cli.client": SimpleNamespace(TwitterClient=factory),
                   "twitter_cli.serialization": SimpleNamespace(tweet_to_dict=lambda t: {"id": t.id}),
                   "twitter_cli.auth": SimpleNamespace(get_cookies=cookie_discovery, extract_from_browser=cookie_discovery)}
        with patch.dict(sys.modules, modules), patch.object(gateway, "credentials", return_value=("auth", "ct0")):
            self.assertEqual(gateway.fetch("search", "chip", 99), [{"id": "123"}])
            self.assertEqual(gateway.fetch("tweet", "123"), [{"id": "123"}])
            with self.assertRaisesRegex(RuntimeError, "invalid_input"):
                gateway.fetch("post", "chip")
        cookie_discovery.assert_not_called()
        client.fetch_search.assert_called_once_with("chip", count=20, product="Latest")
        self.assertEqual(factory.call_args.kwargs["rate_limit_config"]["maxRetries"], 0)

    def test_error_labels_never_include_raw_api_or_config_error(self):
        for error in [RuntimeError("auth_token=secret"), ValueError("ct0=secret")]:
            self.assertEqual(gateway.safe_error(error), "configuration_or_backend_error")
        self.assertEqual(gateway.safe_error(SimpleError()), "not_authenticated")

    def test_configure_refuses_non_tty_before_prompt_or_write(self):
        with patch.object(gateway.sys.stdin, "isatty", return_value=False), patch.object(gateway.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(RuntimeError, "interactive_terminal_required"):
                gateway.configure()
        prompt.assert_not_called()


class SimpleError(RuntimeError):
    error_code = "not_authenticated"


if __name__ == "__main__":
    unittest.main()
