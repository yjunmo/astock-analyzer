"""Agent-Reach 官方路由：Exa via mcporter；网页正文 via Jina Reader。

Agent-Reach 负责工具选型和诊断，实际搜索直接调用它推荐的上游工具。
仅使用项目 MCP 配置，不导入编辑器配置，也不执行模型生成的 shell 命令。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from astock.paths import PROJECT_ROOT as ROOT

CONFIG = ROOT / "config" / "mcporter.json"
SEARCH_TIMEOUT = 20
READER_TIMEOUT = 15
ENGINE = "agent-reach-exa"
READER_ENGINE = "agent-reach-jina"


def mcporter_command() -> list[str]:
    override = os.environ.get("ASTOCK_MCPORTER_PATH", "").strip()
    if override:
        # 环境变量只接受可执行文件路径，不接受带参数的命令串。
        binary = shutil.which(override)
        if not binary:
            raise RuntimeError("mcporter_missing")
        return [binary]
    local = ROOT / ".astock" / "agent-reach-tools" / "node_modules" / "mcporter" / "dist" / "cli.js"
    node = shutil.which("node")
    if local.is_file() and node:
        return [node, str(local)]
    binary = shutil.which("mcporter")
    if binary:
        return [binary]
    raise RuntimeError("mcporter_missing")


def backend_installed() -> bool:
    """只检查本地依赖，不把安装状态当作远端连通状态。"""
    try:
        mcporter_command()
        return CONFIG.is_file()
    except RuntimeError:
        return False


def _valid_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
        return parts.scheme in {"http", "https"} and bool(parts.hostname)
    except ValueError:
        return False


def _clean(value) -> str:
    if isinstance(value, list):
        value = " ".join(str(x) for x in value)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_search_output(output: str) -> list[tuple]:
    """兼容 Exa 的 JSON/MCP envelope 和 Title/URL/Published/Highlights 文本。"""
    try:
        payload = json.loads(output)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        if payload.get("isError") or payload.get("error"):
            raise RuntimeError("exa_tool_error")
        if "structuredContent" in payload:
            return parse_search_output(json.dumps(payload["structuredContent"], ensure_ascii=False))
        if "content" in payload:
            blocks = payload.get("content") or []
            text = "\n\n".join(str(b.get("text") or "") for b in blocks
                               if isinstance(b, dict) and b.get("type") == "text")
            return parse_search_output(text)
        payload = payload.get("results", [])
    if isinstance(payload, list):
        rows = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if _valid_url(url):
                rows.append((_clean(item.get("title") or url)[:160], url,
                             _clean(item.get("highlights") or item.get("text")
                                    or item.get("snippet"))[:600],
                             _clean(item.get("publishedDate") or item.get("published")), ENGINE))
        return rows
    rows = []
    # 仅以相邻的 Title 和 URL 两行分块，避免正文中出现 Title 时误切。
    pattern = r"(?m)^Title:[ \t]*(.*?)\r?\nURL:[ \t]*(https?://[^\s]+)[ \t]*\r?$"
    matches = list(re.finditer(pattern, output or ""))
    for idx, match in enumerate(matches):
        block = output[match.end():matches[idx + 1].start() if idx + 1 < len(matches) else len(output)]
        published = re.search(r"(?m)^Published(?: Date)?:[ \t]*(.*)$", block)
        content = re.search(r"(?m)^(?:Highlights|Text|Content):[ \t]*", block)
        snippet = block[content.end():] if content else ""
        if _valid_url(match.group(2)):
            rows.append((_clean(match.group(1))[:160], match.group(2),
                         _clean(snippet.rstrip("\n- "))[:600],
                         _clean(published.group(1)) if published else "", ENGINE))
    if not rows and (output or "").strip() and not re.search(
            r"no (?:results|search results) found", output, re.I):
        raise RuntimeError("exa_unrecognized_output")
    return rows


def search(query: str, timelimit: str, limit: int = 12) -> list[tuple]:
    days = (int(timelimit[1:]) if re.fullmatch(r"d[1-9]\d{0,2}", timelimit)
            else {"d": 1, "w": 7, "m": 30}.get(timelimit))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days) if days else None
    objective = (
        (f"查找 {cutoff.date().isoformat()} 至 {now.date().isoformat()} 发布的相关信息。"
         if cutoff else "不限定发布日期，按查询的研究主题寻找原始资料。") +
        "优先公司官网、公告、交易所和权威媒体；保留原始链接、发布日期和可核验事实。"
        "遵守查询中的 site: 来源限制，排除无关公司、栏目页和重复转载。"
    )
    args = {"query": query, "objective": objective, "numResults": limit}
    command = [*mcporter_command(), "--config", str(CONFIG), "call", "exa.web_search_exa",
               "--args", json.dumps(args, ensure_ascii=False),
               "--timeout", str(SEARCH_TIMEOUT * 1000), "--output", "text"]
    env = os.environ.copy()
    proxy = env.get("ASTOCK_SEARCH_PROXY", "").strip()
    if proxy:
        env.update({"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy})
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=SEARCH_TIMEOUT + 3,
                                cwd=ROOT, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("exa_timeout") from exc
    if result.returncode:
        # 不把 stderr 或凭据回传给模型、日志或数据库。
        raise RuntimeError("exa_command_failed")
    rows = parse_search_output(result.stdout)
    recent = []
    for row in rows:
        raw = row[3]
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            # 日期只有年月日时以当天结束为边界，避免当天公告被时间点过滤。
            if len(raw) == 10:
                published += timedelta(days=1)
            if (cutoff and published < cutoff) or published > now + timedelta(days=1):
                continue
        except ValueError:
            # 无日期结果保留为线索；不把未知日期伪装成新发布。
            row = (*row[:3], "", row[4])
        recent.append(row)
    return recent


def read_page(url: str, get) -> str:
    """由调用方提供带系统代理及直连回退的 HTTP 请求函数。"""
    response = get("https://r.jina.ai/" + url, READER_TIMEOUT,
                   {"Accept": "text/plain", "X-Respond-With": "content",
                    "X-Retain-Images": "none", "X-Retain-Media": "none",
                    "X-Remove-Selector": "header, footer, nav, aside"})
    response.raise_for_status()
    text = response.text.strip()
    if len(text) < 40 or re.search(r"(?im)^Warning:.*(?:Target URL returned error|requires CAPTCHA)", text):
        raise RuntimeError("jina_empty_or_blocked")
    marker = "Markdown Content:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    if len(text) < 40:
        raise RuntimeError("jina_empty_or_blocked")
    return text
