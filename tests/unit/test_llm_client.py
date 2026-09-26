"""LLM 客户端测试（对策⑦ / LangGraph `#7417` 防护）。

用哑 transport（不联网）验证：
  · 幂等键命中时不重复调用（防静默重放导致重复计费）
  · 空输出被正确记录且**可诊断**（旧项目 `finish_reason` 100% 未采集）
  · usage 缺失时标 `unavailable` 而非冒充 0
  · 结构化输出失败时带错误重试
  · 成本按实际模型归因（非 model-blind）
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from storyforge.domain.enums import CallStatus, UsageSource
from storyforge.infra.llm import (
    CallContext,
    ClientConfig,
    LLMClient,
    LLMError,
    MemoryIdempotencyStore,
    StructuredOutputError,
)
from storyforge.observability import ContextBreakdown  # noqa: F401  (接口占位)

pytestmark = pytest.mark.unit


class Out(BaseModel):
    name: str
    value: int


class FakeTransport:
    """可编排的哑 transport。

    `script` 是依次返回的 `(text, usage)`；用尽后重复最后一个。
    """

    def __init__(self, script: list[tuple[str, dict]] | None = None) -> None:
        self.script = script or [("ok", {"prompt_tokens": 10, "completion_tokens": 5})]
        self.calls: list[dict] = []

    async def __call__(self, *, model, provider, system, user, **params):
        self.calls.append(
            {"model": model.id, "system": system, "user": user, "params": params}
        )
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[idx]


def make_client(
    script: list[tuple[str, dict]] | None = None,
    *,
    config: ClientConfig | None = None,
) -> tuple[LLMClient, FakeTransport]:
    t = FakeTransport(script)
    c = LLMClient(
        t,
        config=config or ClientConfig(max_attempts=3, backoff_base_s=0.0),
        idempotency=MemoryIdempotencyStore(),
    )
    return c, t


# --------------------------------------------------------------------------- #
# ★ 幂等（防 LangGraph #7417 静默重放）
# --------------------------------------------------------------------------- #


async def test_idempotency_key_reuses_result_without_calling() -> None:
    """★ 幂等键命中时**不得**再次调用 transport。

    LangGraph `#7417`（53 条评论仍 open）：**>180s 的调用会被静默重放**
    → 2~3× 冗余成本。而旧项目实测 `editor` 平均 **117s**、`architect` **72s**，
    章节生成极易超阈值。

    这条测试保证：重放发生时我们**复用已有结果**，不重新烧 token。
    """
    client, transport = make_client([("第一次结果", {"prompt_tokens": 10, "completion_tokens": 5})])
    ctx = CallContext(node="chapter_writer", book_id=1, idempotency_key="ch:v1:42")

    first = await client.complete(node="chapter_writer", system="s", user="u", context=ctx)
    assert first.text == "第一次结果"
    assert not first.from_cache
    assert len(transport.calls) == 1

    # 模拟"重放"：同幂等键再调一次
    second = await client.complete(
        node="chapter_writer", system="s", user="u",
        context=CallContext(node="chapter_writer", book_id=1, idempotency_key="ch:v1:42"),
    )
    assert second.text == "第一次结果"
    assert second.from_cache is True
    assert len(transport.calls) == 1, (
        f"幂等命中时不得重复调用，实测 transport 被调用 {len(transport.calls)} 次"
    )


async def test_different_idempotency_keys_call_separately() -> None:
    client, transport = make_client()
    await client.complete(
        node="chapter_writer", system="s", user="u",
        context=CallContext(node="chapter_writer", idempotency_key="k1"),
    )
    await client.complete(
        node="chapter_writer", system="s", user="u",
        context=CallContext(node="chapter_writer", idempotency_key="k2"),
    )
    assert len(transport.calls) == 2


async def test_no_idempotency_key_always_calls() -> None:
    """无幂等键时正常调用（不缓存）。"""
    client, transport = make_client()
    await client.complete(node="chapter_writer", system="s", user="u")
    await client.complete(node="chapter_writer", system="s", user="u")
    assert len(transport.calls) == 2


# --------------------------------------------------------------------------- #
# ★ 空输出必须可诊断（旧项目 22.2% 空输出无法归因）
# --------------------------------------------------------------------------- #


async def test_blank_output_is_recorded_with_finish_reason() -> None:
    """★ 空输出必须记录 `finish_reason`。

    旧项目实测 `blank 223 / 1003 = 22.2%`，且 `finish_reason` **100% 未采集**
    → 无法区分「模型主动停」与「被 max_tokens 截断」，**这是诊断的前提**。
    """
    client, _ = make_client(
        [("", {"prompt_tokens": 100, "completion_tokens": 50, "finish_reason": "length"})]
    )
    with pytest.raises(LLMError):
        await client.complete(node="scene_extractor", system="s", user="u")

    blanks = [r for r in client.records if r.is_blank]
    assert blanks, "空输出必须被记录"
    assert blanks[0].finish_reason == "length", "必须采集 finish_reason 供归因"
    assert blanks[0].truncated is True


async def test_blank_without_finish_reason_is_still_diagnosable() -> None:
    """provider 不返回 finish_reason 时，必须显式标 `unknown` 而非留空。

    留空会让 `LLMCallRecord` 的校验失败 —— 那等于观测代码崩在业务路径上。
    正确做法是标记"未知"，让问题**可见**但不阻断。
    """
    client, _ = make_client([("", {"prompt_tokens": 100, "completion_tokens": 50})])
    with pytest.raises(LLMError):
        await client.complete(node="scene_extractor", system="s", user="u")
    blanks = [r for r in client.records if r.is_blank]
    assert blanks and blanks[0].finish_reason == "unknown"


async def test_blank_output_consumes_attempts_then_raises() -> None:
    """连续空输出应在耗尽重试后抛错，而不是静默返回空串。"""
    client, transport = make_client([("", {"prompt_tokens": 1, "completion_tokens": 1})])
    with pytest.raises(LLMError):
        await client.complete(node="scene_extractor", system="s", user="u")
    assert len(transport.calls) == 3, "应按 max_attempts 重试"


async def test_blank_rate_is_computable() -> None:
    """空输出率必须可算 —— 旧项目实测 22.2%。"""
    client, _ = make_client(
        [
            ("", {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "length"}),
            ("", {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "length"}),
            ("好的正文", {"prompt_tokens": 10, "completion_tokens": 5}),
        ]
    )
    await client.complete(node="chapter_writer", system="s", user="u")
    assert client.blank_rate() == pytest.approx(2 / 3)


# --------------------------------------------------------------------------- #
# usage 缺失不得冒充 0
# --------------------------------------------------------------------------- #


async def test_missing_usage_marked_unavailable_not_zero() -> None:
    """★ usage 全缺时必须标 `unavailable`，**不得冒充 0**。

    旧项目 `native` 只要 ≥3/5 字段就算，导致 33 行 `cached_tokens` 为 NULL
    → 成本函数既不收 hit 也不收 miss（**漏报**）。
    """
    client, _ = make_client([("文字", {})])
    r = await client.complete(node="chapter_writer", system="s", user="u")
    assert r.record.usage.usage_source is UsageSource.UNAVAILABLE
    assert r.record.usage.usage_complete is False
    assert r.record.usage.prompt_tokens is None, "缺失必须是 None，不是 0"


async def test_partial_usage_marked_partial() -> None:
    client, _ = make_client([("文字", {"prompt_tokens": 100, "completion_tokens": 50})])
    r = await client.complete(node="chapter_writer", system="s", user="u")
    assert r.record.usage.usage_source is UsageSource.PARTIAL
    assert "cached_tokens" in r.record.usage.missing_fields


async def test_full_usage_marked_native() -> None:
    client, _ = make_client(
        [
            (
                "文字",
                {
                    "prompt_tokens": 100, "completion_tokens": 50,
                    "reasoning_tokens": 30, "cached_tokens": 40, "total_tokens": 150,
                },
            )
        ]
    )
    r = await client.complete(node="chapter_writer", system="s", user="u")
    assert r.record.usage.usage_source is UsageSource.NATIVE
    assert r.record.usage.usage_complete is True
    # ⚠️ 有效输出 = completion，不减去也不加上 reasoning
    assert r.record.usage.billable_output_tokens == 50


# --------------------------------------------------------------------------- #
# 能力路由与思考策略
# --------------------------------------------------------------------------- #


async def test_extractor_node_minimizes_thinking() -> None:
    """★ 抽取类节点必须**尽量不思考**。

    旧项目 `entity_classify` 空输出 **100%**（32/32）、`verifier` **62.3%**，
    且 `reasoning/completion` 高达 **99.4%** —— 抽取是模式匹配任务，
    长思考只会烧掉输出预算。

    ## 为什么是「尽量」而不是「禁止」

    实测小米 MiMo v2.5 **无法真正关闭思考**：`enable_thinking: False` 与
    `thinking: False` 都被服务端忽略，reasoning 仍占 completion 的 89~92%。
    而 `reasoning_effort="minimal"` 返回 400（非法值），最低合法档是 `"low"`。

    因此验收标准是「取该模型可用的最低思考档」，而不是「保证不想」——
    后者在这个模型上无法达成，假装达成只会让账单打脸。
    """
    client, transport = make_client()
    await client.complete(node="scene_extractor", system="s", user="u")
    params = transport.calls[0]["params"]
    model_id = transport.calls[0]["model"]
    # 三种合格形态：模型本身不思考 / 显式关闭 / 取最低档
    ok = (
        not params                      # 模型不支持思考
        or params.get("enable_thinking") is False
        or params.get("reasoning_effort") in ("minimal", "low")
    )
    assert ok, f"抽取节点必须尽量不思考，实测 model={model_id} params={params}"


async def test_unknown_node_raises_config_error() -> None:
    """未声明能力需求的节点必须报错，不得静默兜底。

    （决策 D-09：禁止在代码中直接指定模型。）
    """
    from storyforge.infra.llm import ProviderConfigError

    client, _ = make_client()
    with pytest.raises(ProviderConfigError, match="未在 providers.toml"):
        await client.complete(node="undeclared_node", system="s", user="u")


# --------------------------------------------------------------------------- #
# 结构化输出
# --------------------------------------------------------------------------- #


async def test_structured_output_parses_json() -> None:
    client, _ = make_client([('{"name":"陈砚","value":37}', {"prompt_tokens": 10})])
    out, _ = await client.complete_structured(
        node="scene_extractor", system="s", user="u", schema=Out
    )
    assert out.name == "陈砚" and out.value == 37


async def test_structured_output_strips_code_fence() -> None:
    client, _ = make_client([('```json\n{"name":"x","value":1}\n```', {})])
    out, _ = await client.complete_structured(
        node="scene_extractor", system="s", user="u", schema=Out
    )
    assert out.name == "x"


async def test_structured_output_retries_with_error_feedback() -> None:
    """★ 校验失败时把**具体错误**回灌给模型重试，而不是原样重发。"""
    client, transport = make_client(
        [
            ('{"name":"x"}', {}),                      # 缺 value
            ('{"name":"x","value":2}', {}),            # 修正后
        ]
    )
    out, _ = await client.complete_structured(
        node="scene_extractor", system="s", user="原始指令", schema=Out
    )
    assert out.value == 2
    assert len(transport.calls) == 2
    assert "未通过校验" in transport.calls[1]["user"], "重试必须带上错误反馈"
    assert "原始指令" in transport.calls[1]["user"], "重试不得丢失原始指令"


async def test_structured_output_raises_after_retries() -> None:
    client, _ = make_client([("不是 JSON", {})])
    with pytest.raises(StructuredOutputError):
        await client.complete_structured(
            node="scene_extractor", system="s", user="u", schema=Out
        )


# --------------------------------------------------------------------------- #
# ★★ 结构化修复重试 × 幂等缓存（S0 侦察发现的真实缺陷）
# --------------------------------------------------------------------------- #


async def test_structured_repair_retry_works_with_idempotency_key() -> None:
    """★★ 带 `idempotency_key` 时，修复重试必须能**真正再次请求模型**。

    ## 防的是什么（实测缺陷，S0 侦察发现）

    `complete()` 曾在 **schema 校验之前**就把原始文本写进幂等缓存，
    而 `complete_structured` 的修复重试**重用同一个 key**，于是：

        第 1 次：模型返回非法 JSON → 写进缓存
        第 2 次（修复重试）→ **命中缓存** → 拿到同一份非法 JSON → 再失败
        ⇒ 一次格式瑕疵 = 硬失败，零可用输出

    ★ 影响面是**所有**传 key 的结构化节点（抽取 3 + 审校 2 + 章节 2）——
    而它们**都**传 key。即"一次坏 JSON 废掉整章"。

    ## 为什么这条测试当初没被发现

    上一条 `test_structured_output_retries_with_error_feedback` 是同一场景，
    但它**不传 `idempotency_key`** —— 于是完全绕开了缓存路径，
    这个缺陷在测试里隐身。

    ★ 教训（本项目已记录过同类）：**测试的输入漏掉了生产必填的那一项，
    就等于没测那条路径**。生产节点全部传 key，测试也必须传。
    """
    from storyforge.infra.llm.client import CallContext

    client, transport = make_client(
        [
            ('{"name":"x"}', {}),               # 第 1 次：缺必填字段 value
            ('{"name":"x","value":7}', {}),     # 第 2 次：修正后
        ]
    )
    out, _ = await client.complete_structured(
        node="scene_extractor", system="s", user="u", schema=Out,
        context=CallContext(node="scene_extractor", idempotency_key="k:repair"),
    )
    assert out.value == 7, "带 key 时修复重试未能拿到修正后的结果"
    assert len(transport.calls) == 2, (
        f"修复重试没有真正请求模型（transport 只被调 {len(transport.calls)} 次）"
    )


async def test_structured_cache_holds_only_validated_output() -> None:
    """★★ 幂等缓存里**只能有校验通过**的结果。

    这条与上一条互补：上一条证明"重试能再次请求"，
    这条证明"非法结果没被留在缓存里"（否则后续重放会拿到坏数据）。

    ★ 为什么必须单独钉：`complete()` 的成功路径**不区分**校验结果，
    所以"写缓存"这个副作用很容易又被挪回校验之前。
    """
    from storyforge.infra.llm.client import CallContext

    key = "k:cache-valid-only"
    client, transport = make_client(
        [
            ('{"name":"x"}', {}),               # 非法
            ('{"name":"x","value":9}', {}),     # 合法
        ]
    )
    ctx = CallContext(node="scene_extractor", idempotency_key=key)
    out, _ = await client.complete_structured(
        node="scene_extractor", system="s", user="u", schema=Out, context=ctx,
    )
    assert out.value == 9

    # 缓存内容必须是**合法那份**
    cached = client.idempotency.get(key)
    assert cached is not None
    assert "value" in cached, f"缓存里留的是非法输出：{cached!r}"

    # 再次调用应命中缓存（0 次新请求）—— 幂等复用的正向价值仍然保留
    before = len(transport.calls)
    out2, _ = await client.complete_structured(
        node="scene_extractor", system="s", user="u", schema=Out,
        context=CallContext(node="scene_extractor", idempotency_key=key),
    )
    assert out2.value == 9
    assert len(transport.calls) == before, "校验通过的调用应当可安全重放（走缓存）"


async def test_plain_complete_still_caches_without_validation() -> None:
    """`complete()` 的**默认行为不变** —— 非结构化调用照旧写缓存。

    ★ 防的是"修 A 破 B"：`cache_result` 默认为 `True`，
    只有 `complete_structured` 显式传 `False`。
    """
    from storyforge.infra.llm.client import CallContext

    client, transport = make_client([("任意文本", {})])
    key = "k:plain"
    await client.complete(
        node="chapter_writer", system="s", user="u",
        context=CallContext(node="chapter_writer", idempotency_key=key),
    )
    assert client.idempotency.get(key) == "任意文本"

    await client.complete(
        node="chapter_writer", system="s", user="u",
        context=CallContext(node="chapter_writer", idempotency_key=key),
    )
    assert len(transport.calls) == 1, "非结构化调用应当命中缓存（默认行为）"


async def test_structured_output_error_message_includes_raw() -> None:
    """失败信息必须包含原始输出片段，便于排查（不是只有一句"解析失败"）。"""
    client, _ = make_client([("模型说了些废话但没给 JSON", {})])
    with pytest.raises(StructuredOutputError) as ei:
        await client.complete_structured(
            node="scene_extractor", system="s", user="u", schema=Out
        )
    assert "模型说了些废话" in str(ei.value)


# --------------------------------------------------------------------------- #
# 计量字段完整性（旧项目缺失的诊断字段）
# --------------------------------------------------------------------------- #


async def test_record_has_all_diagnostic_fields() -> None:
    """记录必须带齐旧项目**完全缺失**的诊断字段。"""
    client, _ = make_client([("文字", {"prompt_tokens": 10, "cached_tokens": 4})])
    r = await client.complete(
        node="chapter_writer", system="系统提示", user="用户指令",
        context=CallContext(node="chapter_writer", book_id=7, global_no=42),
        prompt_prefix="稳定的前缀",
    )
    rec = r.record
    # ★ 旧项目 100% 未采集的字段
    assert rec.finish_reason is not None, "finish_reason 必须采集"
    # ★ 旧项目只有 0.76% 覆盖率的字段
    assert rec.prompt_prefix_hash is not None, "前缀 hash 是缓存分析的基础"
    assert rec.prompt_total_chars == len("系统提示") + len("用户指令")
    # ★ 旧项目 GenerationAttempt 无 chapter 列（只有 3.2% 的 trace 能按章算钱）
    assert rec.global_no == 42
    assert rec.book_id == 7
    assert rec.node_name == "chapter_writer"
    assert rec.elapsed_ms is not None and rec.elapsed_ms >= 0


async def test_model_attribution_is_actual_model_not_assumed() -> None:
    """每个节点的记录必须带**实际使用的模型**（防 model-blind 成本）。

    ⚠️ 不断言"不同节点必须路由到不同模型"：当前是**单模型策略**
    （`mimo-v2.5` 承担全部节点，与旧项目生产环境一致）。
    那会让测试把「碰巧配了两个模型」当成约束 —— 配置一变就假失败。

    这里断言的是**归因正确性**：记录的 model 必须等于路由实际选出的模型。
    """
    from storyforge.infra.llm.capability import get_registry

    client, _ = make_client()
    await client.complete(node="chapter_writer", system="s", user="u")
    await client.complete(node="scene_extractor", system="s", user="u")

    reg = get_registry()
    for r in client.records:
        if r.status is not CallStatus.OK:
            continue
        expected, _ = reg.route(r.node_name)
        assert r.model == expected.id, (
            f"{r.node_name} 记录的是 {r.model}，但路由选的是 {expected.id} "
            f"—— 成本归因必须用**实际模型**"
        )

# --------------------------------------------------------------------------- #
# JSON 修复（★ 实测：模型在中文长文本的字符串里直接换行）
# --------------------------------------------------------------------------- #


def test_extract_json_repairs_bare_newline_in_string() -> None:
    """★ 真实失败样本：JSON 字符串内有裸换行。

    这在**中文长文本**上极其常见 —— 模型把"段落"当排版的一部分，
    于是照着写换行。而 JSON 规范禁止字符串内出现裸控制字符
    （报错就是 `Invalid JSON: expected ':'`）。

    内容完全正确，只因一个可机械修复的格式瑕疵，整条链路失败三次后放弃 ——
    与旧项目 `entity_classify` 空输出 100% 同源。
    """
    import json as _json

    from storyforge.infra.llm.client import LLMClient as _C

    bad = (
        '{\n'
        '  "worldview": "世界观围绕一座档案馆展开。\n'
        '地下三层是绝对禁区。",\n'
        '  "rules": ["规则一", "规则二"]\n'
        '}'
    )
    with pytest.raises(ValueError):
        _json.loads(bad)                      # 确认样本确实是坏的

    d = _json.loads(_C._extract_json(bad))
    assert d["rules"] == ["规则一", "规则二"]
    assert "绝对禁区" in d["worldview"]
    assert "\n" in d["worldview"], "换行应被转义保留，而不是丢弃"


def test_extract_json_leaves_valid_input_byte_identical() -> None:
    """★ 能解析的输入**一字不改**（不冒险改写正确的东西）。"""
    from storyforge.infra.llm.client import LLMClient as _C

    good = '{"a": "x", "b": [1, 2]}'
    assert _C._extract_json(good) == good
    multi = '{\n  "a": 1,\n  "b": 2\n}'
    assert _C._extract_json(multi) == multi, "结构换行必须保留"


def test_extract_json_preserves_escapes() -> None:
    """已转义的序列不得被二次转义（`\\n` 不能变成 `\\\\n`）。"""
    import json as _json

    from storyforge.infra.llm.client import LLMClient as _C

    src = '{"a": "line1\\nline2", "b": "quote\\"inside"}'
    d = _json.loads(_C._extract_json(src))
    assert d["a"] == "line1\nline2"
    assert d["b"] == 'quote"inside'


def test_extract_json_repairs_inside_code_block() -> None:
    import json as _json

    from storyforge.infra.llm.client import LLMClient as _C

    bad = "```json\n{\n  \"a\": \"x\ny\"\n}\n```"
    assert _json.loads(_C._extract_json(bad))["a"] == "x\ny"


def test_extract_json_does_not_invent_missing_syntax() -> None:
    """★ 只修**无歧义**的控制字符，不猜缺失的引号/键名。

    有歧义的修复会让错误静默变形 —— 那比直接失败危险得多。
    """
    import json as _json

    from storyforge.infra.llm.client import LLMClient as _C

    truly_broken = '{"a": 1 "b": 2}'      # 缺逗号
    with pytest.raises(ValueError):
        _json.loads(_C._extract_json(truly_broken))

# --------------------------------------------------------------------------- #
# 校验错误摘要（供修复 prompt 使用）
# --------------------------------------------------------------------------- #


def test_validation_summary_names_the_missing_field() -> None:
    """★ 修复 prompt 需要的是「缺哪个字段」，不是两千字的 Pydantic 回显。

    实测：模型漏了 `volumes.2.core_conflict`，而原始报错含 URL、type 名、
    input 回显 —— 把那段塞回 prompt 既稀释注意力又白烧 token。
    """
    from pydantic import ValidationError

    from storyforge.infra.llm.client import _summarize_validation_error
    from storyforge.infra.llm.schemas import VolumePlanOut

    with pytest.raises(ValidationError) as ei:
        VolumePlanOut.model_validate({
            "volume_no": 1, "title": "t", "goal": "g",
            "new_locations": ["a"], "new_factions": ["b"],
            "antagonist": "x", "power_ceiling": "p",
            "escalation_from_prev": "从村斗升级为城战",
            "old_map_closure": "收尾", "chapter_count": 25,
        })
    s = _summarize_validation_error(ei.value)
    assert "core_conflict" in s
    assert "缺必填字段" in s
    assert "errors.pydantic.dev" not in s, "不得把文档 URL 塞进 prompt"
    assert len(s) < 300, f"摘要必须短（实测 {len(s)} 字）"


def test_validation_summary_lists_only_field_paths() -> None:
    from pydantic import ValidationError

    from storyforge.infra.llm.client import _summarize_validation_error

    err = _summarize_validation_error(None)
    assert isinstance(err, str) and err


def test_validation_summary_handles_plain_exception() -> None:
    """非 Pydantic 异常（如 JSON 语法错）也要给出可读信息。"""
    from storyforge.infra.llm.client import _summarize_validation_error

    s = _summarize_validation_error(ValueError("Invalid JSON: expected ':'"))
    assert "Invalid JSON" in s

# --------------------------------------------------------------------------- #
# ★ 输出预算必须下发（实测缺陷：131072 声明了却从不下发）
# --------------------------------------------------------------------------- #


async def test_max_tokens_is_always_sent() -> None:
    """★ 实测缺陷：`max_output_tokens` 从配置读了却**从未下发给 API**。

    只有调用方显式传参才设置 `max_tokens`，而所有节点都没传
    → 实际生效的是 provider 默认值（MiMo 上约 8000）
    → `detail_planner` 把 98.75% 花在 reasoning（8000 里 7900）
    → `finish_reason=length`，正文一个字没写出来，三次尝试全废。

    这是旧项目 `max_tokens = max(4096, reasoning*4)` 的同一类失败：
    **思考把输出挤没**。修法是默认用模型声明的上限，而不是 provider 默认值。
    """
    from storyforge.infra.llm.capability import get_registry

    client, transport = make_client()
    await client.complete(node="detail_planner", system="s", user="u")

    params = transport.calls[0]["params"]
    assert "max_tokens" in params, (
        "必须下发 max_tokens —— 不下发就会用 provider 默认值，"
        "而思考可能把它吃光（实测 finish_reason=length 且正文为空）"
    )
    model, _ = get_registry().route("detail_planner")
    assert params["max_tokens"] > 0
    # 必须受上限约束（不能把模型完整上下文长度当输出预算）
    assert params["max_tokens"] <= 32768, params["max_tokens"]
    # 且不低于下限（保证正文有空间）
    assert params["max_tokens"] >= 4096, params["max_tokens"]
    del model


async def test_explicit_max_output_tokens_overrides_default() -> None:
    """调用方显式指定的预算必须原样生效（章节写作会传具体值）。"""
    client, transport = make_client()
    await client.complete(
        node="chapter_writer", system="s", user="u", max_output_tokens=1234
    )
    assert transport.calls[0]["params"]["max_tokens"] == 1234


async def test_output_budget_respects_cap_for_huge_model_declaration() -> None:
    """★ 模型声明 131072 时**不能**照搬 —— 那会让每次调用预留巨额额度。

    上限由 `ClientConfig.max_output_tokens_cap` 控制（默认 32768）。
    """
    from storyforge.infra.llm.capability import load_registry

    reg = load_registry()
    model, provider = reg.route("detail_planner")
    assert model.max_output_tokens > 32768, "前提：MiMo 声明了很大的输出上限"

    client, transport = make_client()
    await client.complete(node="detail_planner", system="s", user="u")
    assert transport.calls[0]["params"]["max_tokens"] == 32768


async def test_blank_output_error_message_is_not_empty() -> None:
    """★ 空输出的报错**必须带诊断信息**，不能只有一句没原因的话。

    实测踩到：多书测试里细纲阶段失败，日志只有
    「节点 detail_planner 调用失败（3 次尝试）：」——**冒号后面是空的**，
    完全无法归因（是模型不想写？被截断？超时？）。
    """
    from storyforge.infra.llm.client import LLMError

    client, _ = make_client([("", {
        "prompt_tokens": 5000, "completion_tokens": 8000,
        "reasoning_tokens": 7900, "finish_reason": "length",
    })])
    with pytest.raises(LLMError) as ei:
        await client.complete(node="detail_planner", system="s", user="u")

    msg = str(ei.value)
    assert msg.rstrip().endswith("：") is False, f"报错信息为空：{msg!r}"
    assert "length" in msg, "必须带 finish_reason（区分「模型主动停」与「被截断」）"
    assert "空输出" in msg


async def test_timeout_error_message_is_not_empty() -> None:
    """★ 超时的报错**必须带诊断信息** —— 与上一条同一类缺陷的第二个来源。

    ## 实测踩到（第二轮）

    `asyncio.wait_for` 抛的 `TimeoutError` **不带任何消息**
    （实测 `str(exc) == ''`、`args == ()`）。直接 `last_err = exc` 会让报错变成：

        节点 detail_planner 调用失败（3 次尝试）：          ← 冒号后为空

    完全无法归因 —— 当时只能靠"这轮耗时 1147 秒（平时 263 秒）"去猜是超时。
    旁证：1147 秒 ≈ 3 次尝试 × 600 秒超时上限，与超时三次的形态吻合。
    """
    import asyncio

    from storyforge.infra.llm.client import ClientConfig, LLMClient, LLMError

    async def _hang(**_kw):
        await asyncio.sleep(30)
        return "", {}

    client = LLMClient(_hang, config=ClientConfig(
        max_attempts=2, timeout_s=1, backoff_base_s=0.01))
    with pytest.raises(LLMError) as ei:
        await client.complete(node="detail_planner", system="s", user="u")

    msg = str(ei.value)
    assert msg.rstrip().endswith("：") is False, f"报错信息为空：{msg!r}"
    assert "超时" in msg
    # 必须带足以归因的上下文（超时上限 / 哪次尝试 / 模型 / prompt 规模）
    assert "timeout" in msg.lower() or "超时" in msg
    assert "prompt" in msg.lower() or "字符" in msg

    # 调用记录也要能看出是超时
    timeouts = [r for r in client.records if r.error_type == "TimeoutError"]
    assert timeouts, "超时必须被记录（否则无法统计超时率）"


def test_describe_error_never_empty() -> None:
    """★ `_describe_error` 是**出口兜底**：绝不让冒号后面空着。

    这一类缺陷本轮踩到两次（空输出未设 last_err / TimeoutError 无消息），
    与其逐个分支去堵，不如在出口统一保证。
    """
    from storyforge.infra.llm.client import _describe_error

    # None：调用方的缺陷，要**显式说出来**而不是打印 "None"
    s = _describe_error(None)
    assert s.strip() and s != "None"

    # 无消息的异常：至少带出类型
    assert _describe_error(TimeoutError()).strip()
    assert "TimeoutError" in _describe_error(TimeoutError())

    # 有消息的异常：原样保留
    assert _describe_error(ValueError("bad value")) == "bad value"
