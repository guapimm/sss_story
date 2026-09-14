"""LangChain 传输层测试（P4 补齐：真实模型调用的落地）。

## 本文件防的是什么

1. **usage 字段漏映射** → 成本算错。旧项目 `native` 只要 ≥3/5 字段即算，
   导致 33 行 `cached_tokens` 为 NULL → 成本**漏报**（不是多报）。
   本文件逐字段验证映射，并断言**拿不到就留 None**（绝不冒充 0）。

2. **`content` 是内容块列表时被字符串化** → 结构体当成正文入库。
   Anthropic 风格的 `[{"type":"text","text":"..."}]` 必须被展平。

3. **缺 API key 静默失败** → 空跑一轮才发现。旧项目 `.env` 写的模型
   被 `llm_providers.json` 静默覆盖，缺 key 的行为无人明确。

4. **思考控制未下发** → 抽取节点烧光输出预算。
   旧项目 `reasoning / completion` 达 **99.4%**。
"""

from __future__ import annotations

import pytest

from storyforge.infra.llm.capability import (
    ModelCapability,
    ProviderSpec,
    load_registry,
)
from storyforge.infra.llm.transport import (
    LangChainTransport,
    MissingAPIKeyError,
    extract_text,
    extract_usage,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 哑 AIMessage（不依赖 langchain 内部实现细节）
# --------------------------------------------------------------------------- #


class FakeMessage:
    def __init__(self, content, *, usage_metadata=None, response_metadata=None):
        self.content = content
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}


# --------------------------------------------------------------------------- #
# usage 映射
# --------------------------------------------------------------------------- #


def test_extract_usage_standard_fields() -> None:
    """标准 `usage_metadata` 全字段映射。"""
    msg = FakeMessage(
        "正文",
        usage_metadata={
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "input_token_details": {"cache_read": 40, "cache_creation": 5},
            "output_token_details": {"reasoning": 20},
        },
    )
    u = extract_usage(msg)
    assert u["prompt_tokens"] == 100
    assert u["completion_tokens"] == 50
    assert u["total_tokens"] == 150
    assert u["cached_tokens"] == 40
    assert u["cache_creation_tokens"] == 5
    assert u["reasoning_tokens"] == 20


def test_extract_usage_missing_fields_stay_absent() -> None:
    """★ 拿不到的字段**不得出现**在 dict 里。

    出现即会被当成 0 → 成本漏报（旧项目 33 行 `cached_tokens` 为 NULL
    却按 `native` 计，既不收 hit 也不收 miss）。
    """
    msg = FakeMessage("正文", usage_metadata={"input_tokens": 100})
    u = extract_usage(msg)
    assert u["prompt_tokens"] == 100
    assert "cached_tokens" not in u
    assert "completion_tokens" not in u
    assert "reasoning_tokens" not in u


def test_extract_usage_deepseek_cache_fields() -> None:
    """DeepSeek 用 `prompt_cache_hit_tokens`（非标字段）。"""
    msg = FakeMessage(
        "正文",
        usage_metadata={"input_tokens": 200, "output_tokens": 30},
        response_metadata={
            "usage": {
                "prompt_tokens": 200, "completion_tokens": 30,
                "prompt_cache_hit_tokens": 160,
            },
            "finish_reason": "stop",
        },
    )
    u = extract_usage(msg)
    assert u["cached_tokens"] == 160
    assert u["finish_reason"] == "stop"


def test_extract_usage_openai_nested_cached_tokens() -> None:
    """OpenAI 用 `prompt_tokens_details.cached_tokens`（嵌套）。"""
    msg = FakeMessage(
        "正文",
        response_metadata={
            "usage": {
                "prompt_tokens": 300, "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 256},
            },
        },
    )
    u = extract_usage(msg)
    assert u["cached_tokens"] == 256
    assert u["prompt_tokens"] == 300


def test_extract_usage_finish_reason_from_response_metadata() -> None:
    """★ `finish_reason` 必须采集 —— 旧项目 **100% 未采集**，
    导致 22.2% 空输出无法归因。"""
    msg = FakeMessage("x", response_metadata={"finish_reason": "length"})
    assert extract_usage(msg)["finish_reason"] == "length"


def test_extract_usage_no_metadata_is_empty() -> None:
    """完全没有 usage → 空 dict（→ `usage_source = unavailable`）。"""
    assert extract_usage(FakeMessage("x")) == {}


def test_extract_usage_ignores_non_numeric() -> None:
    msg = FakeMessage("x", usage_metadata={"input_tokens": "n/a", "output_tokens": None})
    u = extract_usage(msg)
    assert "prompt_tokens" not in u
    assert "completion_tokens" not in u


# --------------------------------------------------------------------------- #
# content 展平
# --------------------------------------------------------------------------- #


def test_extract_text_plain_string() -> None:
    assert extract_text(FakeMessage("陈砚推开门。")) == "陈砚推开门。"


def test_extract_text_content_blocks() -> None:
    """★ 内容块列表必须展平。

    直接 `str(content)` 会把结构体字符串化后当正文入库。
    """
    msg = FakeMessage([
        {"type": "text", "text": "第一段。"},
        {"type": "text", "text": "第二段。"},
    ])
    assert extract_text(msg) == "第一段。第二段。"


def test_extract_text_skips_non_text_blocks() -> None:
    """非文本块（如 thinking 块）不得混入正文。"""
    msg = FakeMessage([
        {"type": "thinking", "thinking": "让我想想……"},
        {"type": "text", "text": "正文在此。"},
    ])
    assert extract_text(msg) == "正文在此。"


def test_extract_text_handles_string_blocks() -> None:
    assert extract_text(FakeMessage(["a", "b"])) == "ab"


def test_extract_text_never_returns_none_repr() -> None:
    assert extract_text(FakeMessage(None)) == ""


# --------------------------------------------------------------------------- #
# 客户端构造与思考控制
# --------------------------------------------------------------------------- #


def test_missing_api_key_raises_explicitly(monkeypatch) -> None:
    """★ 缺 key 必须**显式报错**，不静默失败。"""
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    reg = load_registry()
    model, provider = reg.route("chapter_writer")
    t = LangChainTransport(reg)
    with pytest.raises(MissingAPIKeyError, match="API key 未配置"):
        t._build(model, provider, {})


def test_extractors_get_lowest_available_thinking(monkeypatch) -> None:
    """★ 抽取节点必须拿到**该模型可用的最低思考档**。

    旧项目 `entity_classify` 空输出 **100%**、`reasoning/completion` **99.4%**
    —— 抽取是模式匹配任务，长思考只会烧预算并把输出挤没。

    ## 为什么断言的不是「完全不思考」

    实测小米 MiMo v2.5 **关不掉思考**：`enable_thinking: False` 与
    `thinking: False` 都被服务端忽略；`reasoning_effort="minimal"` 返回 400。
    最低合法档是 `"low"`。

    所以合格形态有三种，断言的是**取到最低档**这个效果：
      · 模型不支持思考（`supports_reasoning=False`）→ 无需参数
      · 支持思考且能关 → 必须下发 `enable_thinking=False`
      · 支持思考且关不掉 → 必须取 effort 的最低合法档

    假装"已关闭"而服务端忽略它，只会让账单上的 reasoning 打脸。
    """
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    reg = load_registry()
    for node in ("scene_extractor", "entity_resolver", "relation_extractor"):
        model, _ = reg.route(node)
        policy = reg.thinking_policy_for(node, model)
        if not model.supports_reasoning:
            continue                                  # 天然不想
        if policy.get("enable_thinking") is False:
            continue                                  # 真关掉了
        assert policy.get("reasoning_effort") in ("minimal", "low"), (
            f"{node}（{model.id}）既没有关闭思考，也没有取最低档：policy={policy}"
            f" —— 抽取节点必须尽量不思考"
        )


def test_max_tokens_passed_through(monkeypatch) -> None:
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    reg = load_registry()
    model, provider = reg.route("chapter_writer")
    t = LangChainTransport(reg)
    chat = t._build(model, provider, {"max_tokens": 8192})
    assert chat.max_tokens == 8192


def test_client_is_cached_by_key(monkeypatch) -> None:
    """同一 (provider, model, params) 只建一次连接池。"""
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    reg = load_registry()
    model, provider = reg.route("chapter_writer")
    t = LangChainTransport(reg)
    a = t._build(model, provider, {})
    b = t._build(model, provider, {})
    assert a is b


def test_dataclass_marker() -> None:
    """占位：确保模块级导入的类确实来自能力层（防测试自身写错）。"""
    assert ModelCapability.__name__ == "ModelCapability"
    assert ProviderSpec.__name__ == "ProviderSpec"
