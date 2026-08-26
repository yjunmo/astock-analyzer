"""统一 LLM 客户端：OpenAI 兼容协议 + Anthropic 协议，仅依赖 requests。

- openai 协议：/chat/completions（OpenAI / DeepSeek / Kimi / 千问兼容模式 /
  智谱 GLM / Ollama / 各类中转站）
- anthropic 协议：/v1/messages（Claude 原生）

API Key 仅用于请求头，绝不写入日志或异常文本。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

import requests

DEFAULT_TEMPERATURE = 0.3
# 推理模型的思考链与正文共享该输出预算：思考型模型默认强度即 high，
# 实测 8K 常被纯思考耗尽，故默认 16K；chat_complete 续写时还会自动翻倍
DEFAULT_MAX_TOKENS = 16384
# (连接超时, 读超时)：推理型模型思考期可能长时间不吐正文，读超时需放宽
TIMEOUT_SECONDS = (15, 300)

PROVIDERS = {
    "deepseek": {
        # 官方 base_url 为根地址；如需走 Anthropic 协议，
        # 可在「自定义」中填 https://api.deepseek.com/anthropic
        "label": "DeepSeek", "protocol": "openai",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash",
                   "deepseek-v4-flash-vision-exp",
                   "deepseek-chat", "deepseek-reasoner"],
        # 官方要求：思考模式+工具调用时，后续轮次需回传 reasoning_content
        "reasoning_tools": True,
    },
    "moonshot": {
        "label": "Kimi (月之暗面)", "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k"],
    },
    "dashscope": {
        "label": "通义千问", "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
    },
    "zhipu": {
        "label": "智谱 GLM", "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash"],
    },
    "openai": {
        "label": "OpenAI", "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o"],
    },
    "anthropic": {
        "label": "Claude", "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5"],
    },
    "openrouter": {
        "label": "OpenRouter", "protocol": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["stealth/ox-alpha"],
    },
    "custom": {
        "label": "自定义（Ollama/中转站等）", "protocol": "openai",
        "base_url": "", "models": [],
    },
}


class AIError(RuntimeError):
    """对外可展示的 AI 调用错误。"""


CONFIG_PATH = Path(".streamlit/secrets.toml")
_CONFIG_KEYS = ("provider", "base_url", "model", "api_key")


def load_local_config() -> dict:
    """读取本机 secrets.toml 中的 [ai] 配置；文件不存在或损坏时返回空 dict。"""
    try:
        import tomllib
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        cfg = dict(data.get("ai") or {})
        return {k: str(v) for k, v in cfg.items() if k in _CONFIG_KEYS}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_local_config(cfg: dict) -> Path:
    """把 AI 连接配置写入本机 secrets.toml（明文，仅供个人电脑使用）。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 由 A股技术分析工具写入；API Key 为明文存储，请勿提交仓库或分享该文件。",
        "",
        "[ai]",
    ]
    for k in _CONFIG_KEYS:
        v = str(cfg.get(k, "")).replace('"', "'").replace("\n", " ")
        lines.append(f'{k} = "{v}"')
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return CONFIG_PATH


def clear_local_config() -> bool:
    try:
        CONFIG_PATH.unlink()
        return True
    except FileNotFoundError:
        return False


def _friendly_status(status: int, body: str) -> str:
    if status in (401, 403):
        return f"API Key 无效或无权限（HTTP {status}）"
    if status == 404:
        return "接口路径不存在（HTTP 404），请检查 Base URL 与厂商协议是否匹配"
    if status == 429:
        return "触发限流或额度不足（HTTP 429），请稍后再试"
    snippet = (body or "").strip().replace("\n", " ")[:200]
    return f"HTTP {status}: {snippet}"


def _post_with_retry(url: str, headers: dict, payload: dict,
                     timeout: float = TIMEOUT_SECONDS) -> requests.Response:
    last_err: Optional[Exception] = None
    for _ in range(2):  # 网络级错误重试一次；HTTP 状态错误立即抛出不重试
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 stream=True, timeout=timeout)
            if resp.status_code != 200:
                try:
                    body = resp.text
                except Exception:
                    body = ""
                raise AIError(_friendly_status(resp.status_code, body))
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
    raise AIError(f"网络连接失败：{last_err}")


def _iter_sse(resp: requests.Response) -> Iterator[dict]:
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not raw.startswith("data:"):
            continue
        data = raw[len("data:"):].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _openai_stream(resp: requests.Response) -> Iterator[tuple]:
    """解析 OpenAI 兼容 SSE；推理模型的思考链经 delta.reasoning(.reasoning_content) 输出。

    产出 (kind, text) 元组，kind ∈ {"reasoning", "content"}。
    """
    for obj in _iter_sse(resp):
        err = obj.get("error")
        if err:
            msg = err.get("message", "") if isinstance(err, dict) else str(err)
            raise AIError(f"流式返回错误：{msg}")
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning:
            yield "reasoning", reasoning
        piece = delta.get("content")
        if piece:
            yield "content", piece
        finish = choices[0].get("finish_reason")
        if finish:
            if finish == "length":
                raise AIError(
                    "回复被 max_tokens 截断（finish_reason=length）：推理型模型"
                    "的思考链会消耗同一配额，思考过长时正文可能来不及输出。"
                    "请调大技能 frontmatter 里的 max_tokens 后重试。")
            return


def _anthropic_stream(resp: requests.Response) -> Iterator[tuple]:
    stop_reason = None
    for obj in _iter_sse(resp):
        etype = obj.get("type")
        if etype == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "thinking_delta" and d.get("thinking"):
                yield "reasoning", d["thinking"]
            elif d.get("text"):
                yield "content", d["text"]
        elif etype == "message_delta":
            stop_reason = (obj.get("delta") or {}).get("stop_reason") or stop_reason
        elif etype == "message_stop":
            if stop_reason == "max_tokens":
                raise AIError(
                    "回复被 max_tokens 截断（stop_reason=max_tokens）："
                    "思考链与正文共享配额，请调大 max_tokens 后重试。")
            return
        elif etype == "error":
            raise AIError(f"流式返回错误：{obj.get('error')}")


MAX_BUDGET = 65536          # 自动扩大的输出预算硬上限
MAX_CONTINUATIONS = 6       # 正文截断后续写的最多次数

CONTINUE_INSTRUCTION = (
    "上文你的回复因输出长度限制被截断。请从上次停止的位置直接继续输出剩余内容："
    "不要重复任何已输出的文字，不要重新开头，保持原有格式与语言。"
)

RETRY_STEERING = (
    "注意：上一次尝试因输出预算耗尽而未能产出正文。"
    "本次请大幅精简内部推理过程——只保留决定性的关键判断，"
    "跳过细枝末节的枚举与重复验证，尽快给出完整正文。"
)


def chat_complete(provider_cfg: dict, model: str, api_key: str, messages: list,
                  temperature: float = DEFAULT_TEMPERATURE,
                  max_tokens: int = DEFAULT_MAX_TOKENS,
                  timeout=TIMEOUT_SECONDS,
                  extra_params: Optional[dict] = None) -> Iterator[tuple]:
    """在 chat_stream 之上保证拿到完整正文（不计成本模式）。

    - 零正文即被截断（思考耗尽配额）：预算翻倍静默重试，直至 MAX_BUDGET；
      每次重置前先产出 ("restart", 说明) 事件，UI 应清空已渲染的思考链。
    - 正文写到一半被截断：自动追加"从断点继续"的用户消息无缝续写，
      产出 ("notice", 说明) 事件供 UI 提示；最多 MAX_CONTINUATIONS 轮。
    其余事件为 ("reasoning", text) / ("content", text)。
    """
    budget = min(int(max_tokens), MAX_BUDGET)
    rounds = 0
    while True:
        rounds += 1
        got_content = False
        try:
            for kind, piece in chat_stream(provider_cfg, model, api_key,
                                           messages, temperature=temperature,
                                           max_tokens=budget, timeout=timeout,
                                           extra_params=extra_params):
                if kind == "content":
                    got_content = True
                yield kind, piece
            return
        except AIError as e:
            if "max_tokens" not in str(e):
                raise

        if not got_content:
            doubled = budget * 2
            if doubled > MAX_BUDGET or rounds > 10:
                raise AIError(
                    f"思考链耗尽输出预算且已达上限 {MAX_BUDGET}，模型仍未产出正文。"
                    "请更换更精简的技能、调低思考强度，或换更高输出上限的模型。")
            budget = doubled
            # 重试同时注入"精简推理"引导，避免重新生成同样冗长的思考链（省token）
            messages = messages + [{"role": "user", "content": RETRY_STEERING}]
            yield "restart", f"思考超出配额，输出预算提升至 {budget}，已要求模型精简推理后重试…"
            continue

        if rounds >= MAX_CONTINUATIONS:
            raise AIError(
                f"正文经 {rounds - 1} 次自动续写后仍被截断（累计预算 {budget}）。"
                "请精简提问或更换模型。")
        # 续写轮同步扩大预算：推理模型续写时仍会先思考，原预算往往不够
        budget = min(budget * 2, MAX_BUDGET)
        messages = messages + [{"role": "user",
                                "content": CONTINUE_INSTRUCTION}]
        yield "notice", (f"检测到正文被截断（思考+正文共享配额），"
                         f"预算提升至 {budget} 并已请求从断点续写…")


def chat_stream(provider_cfg: dict, model: str, api_key: str, messages: list,
                temperature: float = DEFAULT_TEMPERATURE,
                max_tokens: int = DEFAULT_MAX_TOKENS,
                timeout=TIMEOUT_SECONDS,
                extra_params: Optional[dict] = None) -> Iterator[tuple]:
    """发起流式对话，产出 (kind, text) 元组；kind ∈ {"reasoning", "content"}。

    extra_params：附加请求字段（如 DeepSeek 的 {"thinking": {"type": "enabled"}}
    与 {"reasoning_effort": "low"}），仅合并进 openai 协议的请求体；
    配置/HTTP 状态错误抛 AIError；流中途断连且尚未产出任何片段时自动重试一次。
    """
    if not api_key:
        raise AIError("未填写 API Key")
    if not model:
        raise AIError("未填写模型名称")
    base = (provider_cfg.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise AIError("未配置 Base URL")

    protocol = provider_cfg.get("protocol", "openai")
    if protocol == "anthropic":
        system_text = "\n\n".join(
            m["content"] for m in messages if m.get("role") == "system")
        conv = [{"role": m["role"], "content": m["content"]}
                for m in messages if m.get("role") != "system"]
        payload = {"model": model, "max_tokens": int(max_tokens),
                   "temperature": float(temperature), "messages": conv,
                   "stream": True}
        if system_text:
            payload["system"] = system_text
        headers = {"x-api-key": api_key,
                   "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        url, parser = base + "/v1/messages", _anthropic_stream
    else:
        payload = {"model": model, "messages": messages,
                   "temperature": float(temperature),
                   "max_tokens": int(max_tokens), "stream": True}
        if extra_params:
            payload.update(extra_params)
        headers = {"Authorization": f"Bearer {api_key}",
                   "Content-Type": "application/json"}
        url, parser = base + "/chat/completions", _openai_stream

    for attempt in range(2):
        produced = False
        try:
            resp = _post_with_retry(url, headers, payload, timeout=timeout)
            for kind, piece in parser(resp):
                produced = True
                yield kind, piece
            return
        except AIError:
            raise  # HTTP 状态/协议层错误不重试
        except (requests.exceptions.ChunkedEncodingError,
                requests.ConnectionError, requests.Timeout) as e:
            if produced or attempt >= 1:
                raise AIError(f"流式连接中断：{e}") from e
            # 未产出任何内容即断流（常见于推理模型长思考被掐断），静默重试一次


def chat_once(provider_cfg: dict, model: str, api_key: str, messages: list,
              temperature: float = DEFAULT_TEMPERATURE,
              max_tokens: int = DEFAULT_MAX_TOKENS,
              timeout=TIMEOUT_SECONDS,
              extra_params: Optional[dict] = None,
              tools: Optional[list] = None) -> dict:
    """非流式单轮对话，供 Agent 工具循环使用。

    返回归一化消息字典：
    {"role":"assistant","content":str,"tool_calls":[...]|[],
     "reasoning_content":str|None,"finish_reason":str}

    tools 为 OpenAI function-calling schema 列表（仅 openai 协议支持；
    anthropic 协议传入时忽略并按普通对话处理）。
    """
    if not api_key:
        raise AIError("未填写 API Key")
    if not model:
        raise AIError("未填写模型名称")
    base = (provider_cfg.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise AIError("未配置 Base URL")

    protocol = provider_cfg.get("protocol", "openai")
    if protocol == "anthropic":
        system_text = "\n\n".join(
            m["content"] for m in messages if m.get("role") == "system")
        conv = [{"role": m["role"], "content": m["content"]}
                for m in messages if m.get("role") != "system"]
        payload = {"model": model, "max_tokens": int(max_tokens),
                   "temperature": float(temperature), "messages": conv,
                   "stream": False}
        if system_text:
            payload["system"] = system_text
        headers = {"x-api-key": api_key,
                   "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        url = base + "/v1/messages"
    else:
        payload = {"model": model, "messages": messages,
                   "temperature": float(temperature),
                   "max_tokens": int(max_tokens), "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if extra_params:
            payload.update(extra_params)
        headers = {"Authorization": f"Bearer {api_key}",
                   "Content-Type": "application/json"}
        url = base + "/chat/completions"

    resp = _post_with_retry(url, headers, payload, timeout=timeout)
    try:
        data = resp.json()
    except Exception as e:
        raise AIError(f"响应解析失败：{e}") from e
    finally:
        resp.close()

    if protocol == "anthropic":
        content_parts, tool_calls = [], []
        for blk in data.get("content") or []:
            btype = blk.get("type")
            if btype == "text":
                content_parts.append(blk.get("text", ""))
            elif btype == "tool_use":
                import json as _json
                tool_calls.append({
                    "id": blk.get("id", ""),
                    "type": "function",
                    "function": {"name": blk.get("name", ""),
                                 "arguments": _json.dumps(blk.get("input") or {},
                                                          ensure_ascii=False)},
                })
        return {"role": "assistant", "content": "\n".join(content_parts),
                "tool_calls": tool_calls, "reasoning_content": None,
                "finish_reason": data.get("stop_reason") or ""}

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {"role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "reasoning_content": msg.get("reasoning_content"),
            "finish_reason": choice.get("finish_reason", "")}
