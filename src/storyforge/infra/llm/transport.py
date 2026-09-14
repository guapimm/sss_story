"""真实 LLM 传输层 —— 基于 **langchain-openai** 的 `ChatOpenAI`。

## 为什么必须有这一层（而不是只有测试用哑 transport）

项目要求「必须基于 langchain 和 langgraph 重构」。LangGraph 已经承担了
图的编排，但**模型调用本身**若走自写的 HTTP 客户端，就等于绕过了 langchain ——
那样"基于 langchain"只是名义上的。

本模块把 `LLMClient` 的 `transport` 接到 `ChatOpenAI` 上：

    LLMClient(transport=LangChainTransport(registry))
        → ChatOpenAI(...).ainvoke(messages)
        → (text, usage_dict)

## 为什么 `transport` 仍是可注入的函数

`LLMClient` 的重试/计量/幂等/结构化修复**全部与传输层解耦**，
因此那些逻辑可以完全离线测试（见 `tests/unit/test_llm_client.py`）。
这一点是刻意的：旧项目 1,003 次调用中有 223 次空输出，
而**没有任何一个测试能在不打网络的情况下复现它** ——
因为它把重试逻辑和 HTTP 请求写在同一处。

## usage 字段的映射（这里是成本准确性的关键）

`ChatOpenAI` 的 `usage_metadata` 是**标准字段**，但各家的缓存字段名不同：

    OpenAI / DeepSeek  : prompt_cache_hit_tokens / prompt_tokens_details.cached_tokens
    Anthropic          : cache_read_input_tokens

本模块把能拿到的都映射到 `UsageMetrics`，
**拿不到的保持 `None`**（→ `usage_source = unavailable/partial`），
绝不冒充 0 —— 旧项目 `native` 只要 ≥3/5 字段就算，
导致 33 行 `cached_tokens` 为 NULL → 成本**漏报**。

## reasoning tokens

DeepSeek 的 `reasoning_content` 与 OpenAI 的 `reasoning_tokens` 都是非标/半标字段。
本模块从 `usage_metadata.output_token_details.reasoning` 与
`response_metadata` 两处尝试读取；读不到就留 `None`。

⚠️ 旧项目实测 `reasoning / completion` 达 **99.4%**（抽取节点），
且 `max_tokens = max(4096, reasoning * 4)` 让思考与输出共用上限
→ 可用输出预算只剩 3x。因此本项目：
  · 抽取节点由能力声明**禁止思考**（`thinking = "forbidden"`）
  · `min_output_tokens` 保证正文不被思考挤没
"""

from __future__ import annotations

import logging
from typing import Any

from storyforge.infra.llm.capability import ModelCapability, ProviderSpec

logger = logging.getLogger(__name__)


class MissingAPIKeyError(RuntimeError):
    """API key 未配置 —— **必须显式报错**。

    旧项目 `.env` 写的模型被 `llm_providers.json` **静默覆盖**，
    且缺 key 时的行为无人明确（可能静默退出、可能空跑）。
    """


def _coerce_int(value: Any) -> int | None:
    """转 int；拿不到就 `None`（**绝不冒充 0**）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_usage(message: Any) -> dict[str, Any]:
    """从 LangChain 的 `AIMessage` 提取 usage —— **单一来源**。

    返回的 dict 直接喂给 `LLMClient._parse_usage`。
    缺失的字段**不出现**在 dict 里（→ `UsageMetrics` 记为 `None`）。
    """
    out: dict[str, Any] = {}

    meta = getattr(message, "usage_metadata", None) or {}
    if isinstance(meta, dict):
        if (v := _coerce_int(meta.get("input_tokens"))) is not None:
            out["prompt_tokens"] = v
        if (v := _coerce_int(meta.get("output_tokens"))) is not None:
            out["completion_tokens"] = v
        if (v := _coerce_int(meta.get("total_tokens"))) is not None:
            out["total_tokens"] = v
        # 缓存命中：标准字段在 input_token_details.cache_read
        details = meta.get("input_token_details") or {}
        if isinstance(details, dict):
            if (v := _coerce_int(details.get("cache_read"))) is not None:
                out["cached_tokens"] = v
            if (v := _coerce_int(details.get("cache_creation"))) is not None:
                out["cache_creation_tokens"] = v
        odetails = meta.get("output_token_details") or {}
        if isinstance(odetails, dict):
            if (v := _coerce_int(odetails.get("reasoning"))) is not None:
                out["reasoning_tokens"] = v

    # 非标字段（DeepSeek / 中转站）：从 response_metadata 兜底
    rmeta = getattr(message, "response_metadata", None) or {}
    if isinstance(rmeta, dict):
        usage = rmeta.get("usage") or rmeta.get("token_usage") or {}
        if isinstance(usage, dict):
            if "cached_tokens" not in out:
                v = _coerce_int(
                    usage.get("prompt_cache_hit_tokens")
                    or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                    or usage.get("cache_read_input_tokens")
                )
                if v is not None:
                    out["cached_tokens"] = v
            if "prompt_tokens" not in out:
                if (v := _coerce_int(usage.get("prompt_tokens"))) is not None:
                    out["prompt_tokens"] = v
            if "completion_tokens" not in out:
                if (v := _coerce_int(usage.get("completion_tokens"))) is not None:
                    out["completion_tokens"] = v
            if "reasoning_tokens" not in out:
                v = _coerce_int(
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                )
                if v is not None:
                    out["reasoning_tokens"] = v
        fr = rmeta.get("finish_reason")
        if fr:
            out["finish_reason"] = fr
    if "finish_reason" not in out:
        fr = getattr(message, "response_metadata", {})
        if isinstance(fr, dict) and fr.get("finish_reason"):
            out["finish_reason"] = fr["finish_reason"]

    return out


def extract_text(message: Any) -> str:
    """把 `AIMessage.content` 规范成纯文本。

    `content` 可能是 `str`，也可能是 **内容块列表**
    （Anthropic 风格：[{"type": "text", "text": "..."}]）。
    旧项目在这里踩过：直接 `str(content)` 会把结构体字符串化后当正文入库。
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif "text" in block:
                    parts.append(str(block["text"]))
        return "".join(parts)
    return str(content) if content is not None else ""


class LangChainTransport:
    """`LLMClient` 的 `transport` 参数的真实实现（走 `ChatOpenAI`）。

    用法：

        registry = load_registry()
        client = LLMClient(LangChainTransport(registry))

    ## 客户端缓存

    每次调用都新建 `ChatOpenAI` 会重复建连接池。本类按
    `(provider, model, params 指纹)` 缓存实例。
    """

    def __init__(self, registry: Any) -> None:
        self.registry = registry
        self._clients: dict[tuple[str, str, str], Any] = {}

    def _build(self, model: ModelCapability, provider: ProviderSpec, params: dict) -> Any:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        api_key = provider.resolve_api_key()
        if not api_key:
            raise MissingAPIKeyError(
                f"provider {provider.name!r} 的 API key 未配置"
                f"（环境变量 {provider.api_key_env}）—— 显式报错而非静默失败"
            )
        key = (provider.name, model.id, repr(sorted(params.items())))
        if key in self._clients:
            return self._clients[key]

        kwargs: dict[str, Any] = {
            "model": model.id,
            "api_key": api_key,
            "base_url": provider.base_url or None,
            "timeout": None,   # 超时由 LLMClient 的 asyncio.wait_for 统一管
            "max_retries": 0,  # 重试由 LLMClient 统一管（否则退避策略叠加）
        }
        # 思考控制：由能力声明翻译而来（能力驱动，不硬编码厂商分支）
        if params.get("enable_thinking") is False:
            kwargs["extra_body"] = {"enable_thinking": False}
        if (effort := params.get("reasoning_effort")) is not None:
            kwargs["reasoning_effort"] = effort
        if (mt := params.get("max_tokens")) is not None:
            kwargs["max_tokens"] = mt
        # ★ 原生 JSON 模式（实测：MiMo 加此参数后严格解析**直接通过**，
        # 不再出现 ```json 包裹 / 缺逗号 / 字符串内裸换行）
        if params.get("json_mode"):
            kwargs["response_format"] = {"type": "json_object"}

        client = ChatOpenAI(**kwargs)
        self._clients[key] = client
        return client

    async def __call__(
        self,
        *,
        model: ModelCapability,
        provider: ProviderSpec,
        system: str,
        user: str,
        **params: Any,
    ) -> tuple[str, dict[str, Any]]:
        """签名与 `LLMClient.transport` 约定一致：返回 `(text, usage_dict)`。"""
        chat = self._build(model, provider, params)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        message = await chat.ainvoke(messages)
        text = extract_text(message)
        usage = extract_usage(message)
        if not text.strip():
            # 空输出必须留 finish_reason，否则无法归因
            # （旧项目 22.2% 空输出、finish_reason 100% 未采集）
            logger.warning(
                "blank from %s/%s: finish_reason=%s",
                provider.name, model.id, usage.get("finish_reason"),
            )
        return text, usage


def make_transport(registry: Any | None = None) -> LangChainTransport:
    """构造真实传输层（默认用全局注册表）。"""
    if registry is None:
        from storyforge.infra.llm.capability import get_registry  # noqa: PLC0415

        registry = get_registry()
    return LangChainTransport(registry)


__all__ = [
    "MissingAPIKeyError",
    "LangChainTransport",
    "make_transport",
    "extract_text",
    "extract_usage",
]
