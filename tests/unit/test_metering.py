"""Token 计量与成本归因测试（对策⑦）。

## 这些测试防的是旧项目实测的四个成本口径缺陷

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | **reasoning 被重复计入** | `trace_service.py:117-120` 算 `completion + reasoning`（而 `reasoning ⊆ completion`，896/897 行验证 `prompt+completion == total`）→ **多收 77% 的输出费** |
| 2 | **两份互相矛盾的 Credits 公式** | `cache_attribution.py:65-74`（NULL-safe）vs `editor_patch_ab.py:199-204`（`usage.get(k) or 0` 把 NULL 变 0 → 整段 prompt 当 miss） |
| 3 | **成本 model-blind** | 只有一套 MiMo 权重，却用在占生产 **61%** 流量的 deepseek 上 |
| 4 | **`native` 是弱保证** | ≥3/5 字段即算 native → 33 行 `cached_tokens` 为 NULL → **漏报**而非多报 |

以及一个观测缺口：**`finish_reason` 100% 未采集**，
导致 22.2% 的空输出**无法归因**。
"""

from __future__ import annotations

import pytest

from storyforge.domain.enums import CallStatus, UsageSource
from storyforge.observability import (
    ContextBreakdown,
    LLMCallRecord,
    ModelPricing,
    UsageMetrics,
    build_report,
    hash_prompt_prefix,
    pricing_for,
    pricing_table,
    record_to_row,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# ★ reasoning ⊆ completion（防旧项目多收 77% 输出费）
# --------------------------------------------------------------------------- #


def test_reasoning_cannot_exceed_completion() -> None:
    """★ 类型层禁止 `reasoning > completion`。

    语义：`reasoning_tokens ⊆ completion_tokens`（不是并列关系）。
    旧项目把它们相加，多收 77% 的输出费。
    """
    with pytest.raises(ValueError, match="reasoning_tokens"):
        UsageMetrics(prompt_tokens=100, completion_tokens=100, reasoning_tokens=200)


def test_cached_cannot_exceed_prompt() -> None:
    with pytest.raises(ValueError, match="cached_tokens"):
        UsageMetrics(prompt_tokens=100, cached_tokens=200)


def test_billable_output_excludes_reasoning_addition() -> None:
    """★ `billable_output_tokens` 必须**等于** completion，不是 completion+reasoning。"""
    u = UsageMetrics(
        prompt_tokens=1000, completion_tokens=500, reasoning_tokens=400,
        cached_tokens=600, total_tokens=1500,
    )
    assert u.billable_output_tokens == 500, "绝不等于 500+400=900"
    assert u.reasoning_share == pytest.approx(0.8)


def test_cost_does_not_double_count_reasoning() -> None:
    """相同 completion 下，reasoning 高低不应改变输出费用。"""
    p = ModelPricing(cached_input=0.001, input=0.1, output=1.0)
    low = p.cost_of(UsageMetrics(prompt_tokens=1000, completion_tokens=500, reasoning_tokens=0))
    high = p.cost_of(UsageMetrics(prompt_tokens=1000, completion_tokens=500, reasoning_tokens=400))
    assert low == pytest.approx(high), (
        f"reasoning 不应改变输出计费：{low} vs {high}（旧项目 completion+reasoning 的错误）"
    )


# --------------------------------------------------------------------------- #
# NULL 处理（防旧项目把 NULL 变 0）
# --------------------------------------------------------------------------- #


def test_missing_fields_are_not_zero() -> None:
    """★ 缺失字段必须标 `unavailable`/`partial`，**不冒充 0**。"""
    empty = UsageMetrics()
    assert empty.usage_source is UsageSource.UNAVAILABLE
    assert empty.usage_complete is False
    assert empty.missing_fields == [
        "prompt_tokens", "completion_tokens", "reasoning_tokens",
        "cached_tokens", "total_tokens",
    ]

    partial = UsageMetrics(prompt_tokens=100, completion_tokens=50)
    assert partial.usage_source is UsageSource.PARTIAL
    assert partial.usage_complete is False
    assert "cached_tokens" in partial.missing_fields


def test_usage_source_and_complete_are_independent() -> None:
    """`usage_source` 与 `usage_complete` 是**两个独立维度**（旧项目混为一谈）。"""
    full = UsageMetrics(
        prompt_tokens=1, completion_tokens=1, reasoning_tokens=1,
        cached_tokens=1, total_tokens=1,
    )
    assert full.usage_source is UsageSource.NATIVE
    assert full.usage_complete is True

    # NATIVE 但 complete=False 不可能（5 字段齐 = complete）；
    # 反过来 PARTIAL + complete=False 才是常态
    partial = UsageMetrics(prompt_tokens=1, completion_tokens=1)
    assert partial.usage_source is UsageSource.PARTIAL
    assert partial.usage_complete is False


def test_unknown_cache_is_charged_conservatively() -> None:
    """★ 不知道命中量时**保守按全部 miss 计**，而不是静默按 0。

    旧项目 `editor_patch_ab.py:199-204` 用 `usage.get(k) or 0` 把 NULL 变 0，
    等于把折扣价当原价 —— 但那实际上**低估**了成本（cached 便宜）。
    正确做法：不知道就按贵的算，并在报告中标注不确定性。
    """
    p = ModelPricing(cached_input=0.001, input=0.3, output=0.9)
    known = p.cost_of(UsageMetrics(prompt_tokens=1000, completion_tokens=100, cached_tokens=800))
    unknown = p.cost_of(UsageMetrics(prompt_tokens=1000, completion_tokens=100))
    assert unknown > known, "未知命中量必须收费更高（保守）"


def test_cache_hit_rate_returns_none_when_unknown() -> None:
    """缺省即不通过：无法计算返回 `None`，不返回 0。"""
    assert UsageMetrics(prompt_tokens=100).cache_hit_rate is None
    assert UsageMetrics().reasoning_share is None


# --------------------------------------------------------------------------- #
# ★ finish_reason 必须可诊断（旧项目 100% 未采集）
# --------------------------------------------------------------------------- #


def test_blank_call_requires_diagnostic_info() -> None:
    """★ BLANK 状态必须带 `finish_reason` 或 `error_type`。

    旧项目实测 223 次 `blank`（22.2%）**无法归因**，因为
    `finish_reason` 100% 未采集 —— 无法区分「模型主动停」与「被 max_tokens 截断」。
    本校验让观测代码漏采时**立即失败**，而不是事后统计才发现。
    """
    with pytest.raises(ValueError, match="BLANK"):
        LLMCallRecord(
            call_id="c1", node_name="writer", model="m", provider="p",
            status=CallStatus.BLANK,
        )


def test_blank_with_finish_reason_is_accepted() -> None:
    r = LLMCallRecord(
        call_id="c1", node_name="writer", model="m", provider="p",
        status=CallStatus.BLANK, finish_reason="length",
    )
    assert r.is_blank
    assert r.truncated is True


def test_truncation_is_detectable_from_finish_reason() -> None:
    r = LLMCallRecord(
        call_id="c1", node_name="writer", model="m", provider="p",
        status=CallStatus.OK, finish_reason="length",
    )
    assert r.truncated is True


# --------------------------------------------------------------------------- #
# 上下文归因（旧项目覆盖率 0.76%）
# --------------------------------------------------------------------------- #


def test_context_breakdown_rejects_unknown_source() -> None:
    """来源必须来自 `CONTEXT_SOURCES` —— 保证归因口径唯一。

    旧项目 `context_breakdown` 的 `relationships` 键**根本不存在**
    （`memory.build_context` 从不返回它），且 `system_instruction` 恒 0。
    """
    with pytest.raises(ValueError, match="未知的上下文来源"):
        ContextBreakdown(tokens={"不存在的来源": 100})


def test_context_breakdown_coverage() -> None:
    bd = ContextBreakdown(
        tokens={"bible": 500, "states": 300, "rag": 200},
        chars={"bible": 6000, "states": 3000, "rag": 2000},
        total_chars=12000,
    )
    assert bd.coverage >= 0.9
    assert len(__import__("storyforge.observability", fromlist=["x"]).CONTEXT_SOURCES) >= 12


# --------------------------------------------------------------------------- #
# 归因键的**可注册扩展点**（0-4）
# --------------------------------------------------------------------------- #


def test_unregistered_source_still_rejected() -> None:
    """★ 放宽扩展 ⇒ **不得**放宽口径唯一性：未注册的键仍然报错。"""
    with pytest.raises(ValueError, match="未知的上下文来源"):
        ContextBreakdown(tokens={"随手编的键": 1})


def test_register_context_source_accepts_new_key() -> None:
    """★ 注册后即可上报 —— 这是 learn 的阻塞点。

    修复前 `CONTEXT_SOURCES` 是**封闭元组**，learn 的 prompt 分层
    （知识点／例题／公式）没有对应键 → learn 首次上报归因即**运行期崩**
    （`ValueError`）。模式能力层应当**不需要改核心模块**就能扩展。
    """
    from storyforge.observability import (
        register_context_source,
        registered_context_sources,
        reset_context_sources,
    )

    key = "test_knowledge_cards"
    try:
        assert key not in registered_context_sources()
        register_context_source(key)
        assert key in registered_context_sources()
        # 注册后就能建归因对象（修复前这里抛 ValueError）
        bd = ContextBreakdown(
            tokens={key: 100, "bible": 50},
            chars={key: 1200, "bible": 600},
            total_chars=1800,
        )
        assert bd.coverage == pytest.approx(1.0)
    finally:
        reset_context_sources()
    assert key not in registered_context_sources()


def test_register_context_source_is_idempotent() -> None:
    """重复注册不报错、不产生重复项（幂等）。"""
    from storyforge.observability import (
        register_context_source,
        registered_context_sources,
        reset_context_sources,
    )

    try:
        register_context_source("dup_key", "dup_key")
        register_context_source("dup_key")
        srcs = registered_context_sources()
        assert srcs.count("dup_key") == 1
    finally:
        reset_context_sources()


def test_register_context_source_does_not_shadow_builtin() -> None:
    """注册一个**已内置**的键不得产生重复项。"""
    from storyforge.observability import (
        register_context_source,
        registered_context_sources,
        reset_context_sources,
    )

    try:
        register_context_source("bible")
        assert registered_context_sources().count("bible") == 1
    finally:
        reset_context_sources()


def test_register_context_source_rejects_blank() -> None:
    """空键/非字符串必须显式拒绝（否则归因表会出现空列）。"""
    from storyforge.observability import register_context_source

    for bad in ("", "   ", None, 123):
        with pytest.raises(ValueError, match="归因键必须是非空字符串"):
            register_context_source(bad)  # type: ignore[arg-type]


def test_context_sources_builtin_set_is_preserved() -> None:
    """★ 内置 15 键必须**原样保留** —— 改的是扩展方式，不是口径。

    这 15 个键是既有口径（`doc/04-数据模型规格.md` 的
    `context_breakdown` 示例键），动它们会让历史数据无法解释。
    """
    from storyforge.observability import CONTEXT_SOURCES, registered_context_sources

    assert isinstance(CONTEXT_SOURCES, tuple)
    assert len(CONTEXT_SOURCES) == 15
    for k in ("system", "bible", "volume", "outline", "states", "relations",
              "injuries", "foreshadow", "timeline", "rag", "previous_chapter",
              "summary", "style", "instruction", "other"):
        assert k in CONTEXT_SOURCES, f"内置键 {k} 丢了"
    # 未注册时，生效集合 == 内置集合
    assert registered_context_sources() == CONTEXT_SOURCES


# --------------------------------------------------------------------------- #
# 成本按模型（防 model-blind）
# --------------------------------------------------------------------------- #


def test_pricing_is_config_driven_not_hardcoded() -> None:
    """★ 单价必须从 `config/providers.toml` 读，代码里不得出现模型名。

    这条防的是旧项目的 model-blind 成本：只有一套 MiMo 权重，
    却用在占生产 61% 流量的 deepseek 上。
    「代码里不得出现模型名」由 `tests/contract` 规则 4 独立强制。
    """
    table = pricing_table()
    assert table, "必须能从配置读到单价表"
    # 配置里的每个模型都应有非零单价
    for model, p in table.items():
        assert p.input > 0 or p.output > 0, f"{model} 未配置单价"


def test_unknown_model_uses_fallback_not_zero() -> None:
    p = pricing_for("definitely-not-a-configured-model")
    assert p.output > 0 and p.input > 0, "未知模型必须用兜底单价，不能静默按 0"


# --------------------------------------------------------------------------- #
# 报告聚合
# --------------------------------------------------------------------------- #


def _mk_records(n: int = 20, blank: int = 4) -> list[LLMCallRecord]:
    out = []
    for i in range(n):
        is_blank = i < blank
        out.append(
            LLMCallRecord(
                call_id=f"c{i}", node_name="writer" if i % 2 else "extractor",
                model="deepseek-flash", provider="deepseek",
                status=CallStatus.BLANK if is_blank else CallStatus.OK,
                finish_reason="length" if is_blank else "stop",
                usage=UsageMetrics(
                    prompt_tokens=10000, cached_tokens=4000,
                    completion_tokens=5000, reasoning_tokens=3800,
                    total_tokens=15000,
                ),
                context_breakdown=ContextBreakdown(
                    tokens={"bible": 100}, chars={"bible": 1000}, total_chars=1000
                ),
            )
        )
    return out


def test_report_computes_blank_rate() -> None:
    """空输出率 —— 旧项目实测 **22.2%**（`entity_classify` **100%**）。"""
    rep = build_report(_mk_records(20, blank=4))
    assert rep.calls == 20
    assert rep.blank_calls == 4
    assert rep.blank_rate == pytest.approx(0.2)


def test_report_computes_reasoning_share() -> None:
    """reasoning 占 completion —— 旧项目实测 **76.7%**（verifier 99.4%）。"""
    rep = build_report(_mk_records(10, blank=0))
    assert rep.reasoning_share == pytest.approx(3800 / 5000)


def test_report_computes_cache_hit_rate() -> None:
    rep = build_report(_mk_records(10, blank=0))
    assert rep.cache_hit_rate == pytest.approx(4000 / 10000)


def test_report_attribution_coverage() -> None:
    """归因覆盖率 —— 旧项目实测 **0.76%**（等于没有）。"""
    rep = build_report(_mk_records(10, blank=0))
    assert rep.attribution_coverage == pytest.approx(1.0)


def test_report_cost_breakdown_matches_old_structure() -> None:
    """成本拆分应复现旧项目的结构：output 是最大口子。

    旧项目实测：`hit 0.35% / miss 22.15% / output 77.50%`。
    这条测试确认**成本归因本身**是对的（不是"命中率不重要"，
    而是"命中的绝对金额太小"）。
    """
    rep = build_report(_mk_records(20, blank=0))
    cb = rep.cost_breakdown()
    total = sum(cb.values())
    assert total > 0
    assert cb["hit"] / total < 0.05, "缓存命中占比应极小（旧项目 0.35%）"
    assert cb["output"] / total > 0.5, "output 应是最大口子（旧项目 77.5%）"


def test_report_by_node_and_model() -> None:
    rep = build_report(_mk_records(10, blank=0))
    assert set(rep.by_node) == {"writer", "extractor"}
    assert "deepseek-flash" in rep.by_model
    assert rep.by_model["deepseek-flash"]["calls"] == 10


def test_row_roundtrip_preserves_semantics() -> None:
    """记录 → 行 → 记录 必须保语义（尤其 reasoning/completion 的包含关系）。"""
    rec = _mk_records(1, blank=0)[0]
    row = record_to_row(rec)
    # 逐字段照抄，绝不相加
    assert row["reasoning_tokens"] == 3800
    assert row["completion_tokens"] == 5000
    assert row["finish_reason"] == "stop"
    assert row["usage_source"] == "native"
    assert row["context_breakdown"] is not None


def test_prompt_prefix_hash_is_stable() -> None:
    """稳定前缀 hash 必须确定性（用于缓存命中分析）。"""
    assert hash_prompt_prefix("abc") == hash_prompt_prefix("abc")
    assert hash_prompt_prefix("abc") != hash_prompt_prefix("abd")
