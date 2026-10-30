"""Local, read-only Twitter/X gateway using Agent-Reach's explicit credentials.

Never calls upstream get_cookies(), browser extraction, or write operations.
Run with the project's Agent-Reach Python. No credential command-line arguments.
"""
from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def credentials(config=None) -> tuple[str, str]:
    if config is None:
        from agent_reach.config import Config
        config = Config(read_only=True)
    # Preserve upstream explicit environment precedence; never use browser state.
    return (str(os.environ.get("TWITTER_AUTH_TOKEN") or config.get("twitter_auth_token") or ""),
            str(os.environ.get("TWITTER_CT0") or config.get("twitter_ct0") or ""))


def configure() -> dict:
    if not sys.stdin.isatty():
        raise RuntimeError("interactive_terminal_required")
    from agent_reach.cli import _parse_twitter_cookie_input
    from agent_reach.config import Config
    value = getpass.getpass("粘贴 Cookie-Editor Header String（输入不回显）：")
    auth, ct0 = _parse_twitter_cookie_input(value.strip())
    if not auth or not ct0 or any(c.isspace() or c in ";" for c in auth + ct0):
        raise RuntimeError("invalid_cookie_header")
    config = Config()
    # One atomic owner-only write, preserving all other Agent-Reach settings.
    config.data.update(twitter_auth_token=auth, twitter_ct0=ct0)
    config.save()
    return {"ok": True, "configured": True, "verified": False,
            "message": "Cookie 已保存到 Agent-Reach 本地配置；尚未联网验证。"}


def status() -> dict:
    auth, ct0 = credentials()
    return {"ok": True, "installed": True,
            "version": importlib.metadata.version("twitter-cli"),
            "configured": bool(auth and ct0), "verified": False,
            "browser_cookie_fallback": False, "read_only": True}


def fetch(command: str, query: str, count: int = 10) -> list[dict]:
    # Guard again for callers that don't go through argparse.
    if command not in {"search", "tweet"} or not query or len(query) > 1000:
        raise RuntimeError("invalid_input")
    if command == "tweet" and not re.fullmatch(r"\d{1,25}", query):
        raise RuntimeError("invalid_input")
    auth, ct0 = credentials()
    if not auth or not ct0:
        raise RuntimeError("not_configured")
    from twitter_cli.client import TwitterClient
    from twitter_cli.serialization import tweet_to_dict
    # Direct client construction bypasses *all* upstream auth fallback paths.
    client = TwitterClient(auth, ct0, rate_limit_config={
        "requestDelay": 2.5, "maxRetries": 0, "maxCount": 20})
    count = max(1, min(int(count), 20))
    if command == "search":
        tweets = client.fetch_search(query, count=count, product="Latest")
    else:
        # Reply text is not a substitute for the requested original tweet.
        tweets = [t for t in client.fetch_tweet_detail(query, count=20) if str(t.id) == query]
    return [tweet_to_dict(t) for t in tweets[:count]]


def safe_error(error: Exception) -> str:
    allowed = {"interactive_terminal_required", "invalid_cookie_header", "invalid_input",
               "not_configured", "not_authenticated", "rate_limited", "not_found",
               "network_error", "query_id_error", "api_error"}
    code = getattr(error, "error_code", None)
    if code in allowed:
        return code
    if isinstance(error, RuntimeError) and str(error) in allowed:
        return str(error)
    return "dependency_missing" if isinstance(error, ImportError) else "configuration_or_backend_error"


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent-Reach Twitter：本地配置、只读搜索与推文读取")
    parser.add_argument("command", choices=["configure", "status", "search", "tweet"])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--max", type=int, default=10, dest="count")
    args = parser.parse_args()
    if args.command in {"configure", "status"} and args.query is not None:
        parser.error("配置凭据不能放在命令行参数中")
    if args.command in {"search", "tweet"} and not args.query:
        parser.error("请提供搜索词或推文 ID")
    # No upstream verbose logs or exception text: they may contain HTTP/proxy data.
    logging.disable(logging.CRITICAL)
    try:
        if args.command == "configure":
            result = configure()
        elif args.command == "status":
            result = status()
        else:
            result = {"ok": True, "data": fetch(args.command, args.query, args.count)}
    except (KeyboardInterrupt, EOFError):
        result = {"ok": False, "error": "cancelled"}
    except Exception as error:
        result = {"ok": False, "error": safe_error(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
