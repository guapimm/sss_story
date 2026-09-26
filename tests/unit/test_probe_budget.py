"""`ProbeBudget` 的**隔离**单测 —— 不经 `LLMClient`。

## 为什么需要一个独立文件

阶段 A 的 12 条预算测试**全部走 `LLMClient`**。那是端到端的好测试，
但它测的是"客户端在预算下行为正确"，不是"预算记账本身正确"。两者混淆时：

  · 记账算错（如 `>=` 写成 `>`）可能被客户端某个分支掩盖；
  · 想验证"费用恰好等于上限时应当**拦下下一发**"这类边界，必须先构造
    一整套 client + transport + 路由，成本高到没人愿意写。

本文件直接测 `ProbeBudget` 的状态机，覆盖三条**语义边界**：

  1. **恰好等于上限 → 不再放行下一次请求**（不是 `>` 才拦）
  2. **最后一次请求允许越线**，但之后必须停机（负责人明确接受的取舍）
  3. **缺 usage / usage 非法 → 停机而非猜测记账**

## 为什么这些边界值得单独钉

成本闸门的失效方向是**不对称**的：保守（少放行一次）只损失一次调用，
激进（多放行）会真金白银地烧。所以"恰好等于上限"这类边界必须明确，
不能靠读代码推断。
"""

from __future__ import annotations

import dataclasses

import pytest

from storyforge.infra.llm.capability import ModelCapability
from storyforge.infra.llm.probe_budget import ProbeBudget, ProbeBudgetError
from storyforge.observability.metering import ModelPricing, UsageMetrics

pytestmark = pytest.mark.unit

#: MiMo 的真实点数单价（`providers.toml`：hit=2 / miss=100 / output=200）
CREDITS = ModelPricing(cached_input=2.0, input=100.0, output=200.0, currency="CREDITS")


def _model(*, currency="CREDITS", has_pricing=True) -> ModelCapability:
    """取一个**真实**能力声明再改价格字段。

    ★ 刻意不手写 `ModelCapability(...)`：它有 9 个必填字段，手写会在
    每次新增字段时集体报错，而这些测试并不关心那些字段。
    用 `dataclasses.replace` 只覆盖关心的两个，既稳又短。
    """
    from storyforge.infra.llm.capability import get_registry

    base = get_registry().models[0]
    if has_pricing:
        return dataclasses.replace(
            base, pricing_cached_input=2.0, pricing_input=100.0,
            pricing_output=200.0, pricing_currency=currency,
        )
    return dataclasses.replace(
        base, pricing_cached_input=0.0, pricing_input=0.0,
        pricing_output=0.0, pricing_currency=currency,
    )


def _usage(prompt=1000, completion=500, cached=None) -> UsageMetrics:
    return UsageMetrics(
        prompt_tokens=prompt, cached_tokens=cached,
        completion_tokens=completion,
        total_tokens=(prompt or 0) + (completion or 0),
    )


# --------------------------------------------------------------------------- #
# 启用语义
# --------------------------------------------------------------------------- #


def test_all_none_means_disabled() -> None:
    """★ 三个参数全 None = 不启用（负责人裁决：不设默认上限）。"""
    b = ProbeBudget()
    assert not b.enabled
    assert b.prepare(_model(), 10_000) is None   # 不检查、不报错
    b.reserve_attempt(None, 100)                  # 不计数
    assert b.provider_attempts == 0
    assert b.observe_usage(_usage(), CREDITS) is None


@pytest.mark.parametrize("kwargs", [
    {"max_provider_requests": 1},
    {"max_prompt_chars": 1},
    {"max_configured_credits": 1.0},
])
def test_any_single_limit_enables_the_gate(kwargs) -> None:
    assert ProbeBudget(**kwargs).enabled


# --------------------------------------------------------------------------- #
# 请求数：恰好等于上限即拦
# --------------------------------------------------------------------------- #


def test_request_cap_blocks_on_exactly_reaching_it() -> None:
    """★ 边界：请求数**达到**上限后，下一发必须被拦（不是 `>` 才拦）。

    写反成 `>` 会让第 N+1 发漏出去 —— 成本闸门最典型的 off-by-one。
    """
    b = ProbeBudget(max_provider_requests=2)
    b.reserve_attempt(None, 100)                  # 第 1 发
    b.reserve_attempt(None, 100)                  # 第 2 发（用满）
    assert b.provider_attempts == 2
    with pytest.raises(ProbeBudgetError, match="provider request budget reached"):
        b.reserve_attempt(None, 100)              # 第 3 发必须拒绝
    assert b.budget_stopped


def test_stopped_budget_refuses_further_reservations() -> None:
    """停机后即使还有余额也一律拒绝（停机是终态）。"""
    b = ProbeBudget(max_provider_requests=10)
    b.stop("manual")
    with pytest.raises(ProbeBudgetError, match="budget stopped"):
        b.reserve_attempt(None, 100)


def test_stop_keeps_the_first_reason() -> None:
    """首个停机原因不被后续覆盖（否则根因被掩盖）。"""
    b = ProbeBudget(max_provider_requests=1)
    b.stop("first")
    b.stop("second")
    assert b.budget_stop_reason == "first"


# --------------------------------------------------------------------------- #
# prompt 字符数
# --------------------------------------------------------------------------- #


def test_prompt_chars_blocks_before_reaching_provider() -> None:
    b = ProbeBudget(max_prompt_chars=100)
    with pytest.raises(ProbeBudgetError, match="prompt character budget exceeded"):
        b.prepare(_model(), 101)
    assert b.provider_attempts == 0              # 关键：没有消耗请求配额
    b.prepare(_model(), 100)                      # 恰好等于上限 = 放行


# --------------------------------------------------------------------------- #
# 输出 token 上限
# --------------------------------------------------------------------------- #


def test_explicit_output_tokens_above_cap_is_rejected() -> None:
    b = ProbeBudget(max_provider_requests=5)
    with pytest.raises(ProbeBudgetError, match="max_output_tokens exceeds"):
        b.reserve_attempt(101, 100)


def test_none_output_tokens_skips_the_cap_check() -> None:
    """未显式指定输出预算时不检查（由客户端按模型声明取值）。"""
    b = ProbeBudget(max_provider_requests=5)
    b.reserve_attempt(None, 100)
    assert b.provider_attempts == 1


# --------------------------------------------------------------------------- #
# CREDITS 记账
# --------------------------------------------------------------------------- #


def test_credits_accounting_matches_pricing_formula() -> None:
    """点数按 `ModelPricing` 公式累计（hit 折扣必须生效）。"""
    b = ProbeBudget(max_configured_credits=1000.0)
    pricing = b.prepare(_model(), 10)
    assert pricing is not None
    # prompt 1000 全 miss = 1000/1e6*100 = 0.1 点
    # completion 500    =  500/1e6*200 = 0.1 点
    b.observe_usage(_usage(prompt=1000, completion=500), pricing)
    assert b.configured_credits == pytest.approx(0.2)


def test_credits_can_cross_on_last_request_then_stops() -> None:
    """★★ 负责人明确接受的取舍：**最后一次请求可能越线**，之后必须停机。

    事前无法知道一发会花多少（要先拿到 usage），所以只能用"事后停机"。
    这条把该语义钉死，避免后人误以为闸门是硬上限而去掉事后检查。
    """
    b = ProbeBudget(max_configured_credits=0.1)
    pricing = b.prepare(_model(), 10)
    reason = b.observe_usage(_usage(prompt=1000, completion=500), pricing)
    assert reason is not None
    assert "may cross" in reason                  # 明确承认越线
    assert b.configured_credits == pytest.approx(0.2)   # 确实越了
    assert b.budget_stopped
    with pytest.raises(ProbeBudgetError, match="budget stopped"):
        b.reserve_attempt(None, 100)              # 后续一律拒绝


def test_credits_exactly_at_cap_blocks_next_request() -> None:
    """★ 边界：**恰好等于**上限时，下一发必须被拦（不是 `>`）。"""
    b = ProbeBudget(max_configured_credits=0.2)
    pricing = b.prepare(_model(), 10)
    assert b.observe_usage(_usage(prompt=1000, completion=500), pricing) is None
    assert b.configured_credits == pytest.approx(0.2)
    with pytest.raises(ProbeBudgetError, match="budget reached before provider request"):
        b.reserve_attempt(None, 100)
    assert b.budget_stopped


# --------------------------------------------------------------------------- #
# 无法记账 → 停机而非猜测
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", ["prompt_tokens", "completion_tokens"])
def test_missing_usage_stops_instead_of_guessing(missing: str) -> None:
    """★ 缺 usage 时必须停机 —— 猜一个数字等于闸门失效。"""
    b = ProbeBudget(max_provider_requests=5, max_configured_credits=1000.0)
    pricing = b.prepare(_model(), 10)
    u = _usage()
    object.__setattr__(u, missing, None)
    reason = b.observe_usage(u, pricing)
    assert reason is not None and "usage missing" in reason
    assert b.unknown_usage and b.budget_stopped


def test_fails_closed_when_model_has_no_credits_pricing() -> None:
    """★★ 启用预算但模型**没有 CREDITS 单价** → 必须停机（fail-closed）。

    反向做法（当作 0 点、照常跑）会让预算形同虚设 —— 而"S0 要控成本"
    正是启用它的唯一理由。
    """
    b = ProbeBudget(max_provider_requests=5)
    with pytest.raises(ProbeBudgetError, match="requires priced CREDITS"):
        b.prepare(_model(currency="USD"), 10)
    assert b.budget_stopped


def test_fails_closed_when_model_has_no_pricing_at_all() -> None:
    b = ProbeBudget(max_provider_requests=5)
    with pytest.raises(ProbeBudgetError, match="requires priced CREDITS"):
        b.prepare(_model(has_pricing=False), 10)
    assert b.budget_stopped
