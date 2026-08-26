"""联网搜索 Agent：DeepSeek 网页版"联网搜索"模式的工具循环实现。

流程：
  1. 携带 web_search / read_page 工具发起非流式请求
  2. 若模型返回 tool_calls → 执行工具、以 tool 角色回传结果，进入下一轮
  3. 模型给出正文即结束；超过 MAX_TOOL_ROUNDS 轮仍在调工具则报错

事件流（供 UI 渲染）：
  ("status", "🔍 第1轮 · web_search: 天洋新材 半年报")
  ("sources", [(标题, url), ...])
  ("answer", 完整正文)

约束：
- OpenRouter :online 原生联网模式（mode="native"）不加 tools，
  仅在模型名追加 :online 后缀由厂商代执行；来源由模型在正文中标注
- 工具仅支持 openai 协议；anthropic 协议自动降级为普通对话并提示
- DeepSeek 思考+工具合规：assistant 回传时携带 reasoning_content
- 截断防护复用预算翻倍逻辑（finish_reason=length 时同轮重试）
"""
from __future__ import annotations

import json as _json
import re as _re
from typing import Iterator, Optional

from ai_client import (AIError, DEFAULT_MAX_TOKENS, MAX_BUDGET,
                       chat_once)
from search_tools import ALL_TOOLS, execute_tool, extract_sources

MAX_TOOL_ROUNDS = 4

AGENT_SYSTEM_NOTE = (
    "\n\n【联网搜索规范】你已接入 web_search(关键词, freshness∈{day,week,month}) "
    "与 read_page(url) 两个工具。当问题涉及时效信息、动态数据、新闻事件或需要事实核查时，"
    "请自主判断并发起搜索；可多轮调用、更换关键词筛选。回答中引用的网络信息必须以"
    " [n] 标注，并在末尾列出对应来源标题与链接。未使用网络信息时无需列来源。"
)


def run_agent(provider_cfg: dict, model: str, api_key: str,
              messages: list, extra_params: Optional[dict] = None,
              mode: str = "on", max_rounds: int = MAX_TOOL_ROUNDS,
              budget: Optional[int] = None) -> Iterator[tuple]:
    """执行带联网工具的 Agent 循环，产出 (kind, payload) 事件。

    kind ∈ {"status","sources","answer"}；配置/网络错误抛 AIError。
    """
    model_eff = model
    tools = None
    if mode == "native":
        model_eff = model if ":online" in model else f"{model}:online"
    else:
        if provider_cfg.get("protocol") != "openai":
            yield ("status", "⚠️ 当前厂商协议不支持工具调用，已按普通模式回答")
        else:
            tools = ALL_TOOLS
            # 注入联网使用规范（追加到首条 system 之后不影响既有技能内容）
            msgs = list(messages)
            if msgs and msgs[0].get("role") == "system":
                msgs[0] = dict(msgs[0],
                               content=msgs[0]["content"] + AGENT_SYSTEM_NOTE)
            messages = msgs

    collected_sources: list = []
    answer_parts: list = []
    work = list(messages)
    budget_now = min(budget or DEFAULT_MAX_TOKENS, MAX_BUDGET)

    for rnd in range(1, max_rounds + 2):
        b = budget_now
        while True:  # 预算不足即抛错的兜底（chat_once 不自行处理截断）
            try:
                msg = chat_once(provider_cfg, model_eff, api_key, work,
                                extra_params=extra_params, tools=tools,
                                max_tokens=b)
                break
            except AIError as e:
                if "max_tokens" in str(e):
                    nb = min(b * 2, MAX_BUDGET)
                    if nb == b:
                        raise
                    b = nb
                    continue
                raise

        tcs = msg.get("tool_calls") or []
        if not tcs:
            content = msg.get("content", "")
            finish = str(msg.get("finish_reason") or "")
            # 非流式模式的截断防护：length/max_tokens → 扩预算续写
            if finish in ("length", "max_tokens"):
                if content:
                    answer_parts.append(content)
                nb = min(b * 2, MAX_BUDGET)
                if nb == b or rnd >= max_rounds + 1:
                    raise AIError(
                        f"回复被输出配额截断且预算已达上限 {MAX_BUDGET}。"
                        "请调低思考强度或精简问题。")
                budget_now = nb
                work.append({"role": "assistant", "content": content})
                work.append({"role": "user",
                             "content": ("上文回复因长度限制中断。请从停止处"
                                         "直接继续，不要重复已输出内容。")})
                yield ("status", f"✂️ 正文被截断，预算提升至 {budget_now} 续写…")
                continue
            answer_parts.append(content)
            yield ("sources", collected_sources)
            yield ("answer", "".join(answer_parts))
            return

        entry = {"role": "assistant", "content": msg.get("content") or "",
                 "tool_calls": tcs}
        rc = msg.get("reasoning_content")
        if rc and provider_cfg.get("reasoning_tools"):
            entry["reasoning_content"] = rc
        work.append(entry)

        for tc in tcs:
            fn = (tc.get("function") or {})
            fname = fn.get("name", "")
            try:
                args = _json.loads(fn.get("arguments") or "{}")
            except _json.JSONDecodeError:
                args = {}
            brief = args.get("query") or args.get("url") or fname
            yield ("status", f"🔍 第{rnd}轮 · {fname}: {brief}")
            result_text = execute_tool(fname, args)
            if fname == "web_search":
                for title, url in extract_sources(result_text):
                    if url not in [u for _, u in collected_sources]:
                        collected_sources.append((title, url))
            work.append({"role": "tool",
                         "tool_call_id": tc.get("id", ""),
                         "content": result_text})
        budget_now = min(budget_now * 2, MAX_BUDGET)  # 续写/下轮预算同步扩容

    raise AIError(f"已达最大工具轮次({max_rounds})仍未给出最终回答，"
                  "请精简问题后重试。")


def parse_inline_sources(answer: str) -> list:
    """从正文中解析 Markdown 链接作为兜底来源（:online 模式无 tool 事件）。"""
    out = []
    for m in _re.finditer(r"\[([^\]]{4,60})\]\((https?://[^)]+)\)", answer):
        if (m.group(1), m.group(2)) not in out:
            out.append((m.group(1), m.group(2)))
    return out[:8]
