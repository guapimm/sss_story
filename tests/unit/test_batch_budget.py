"""批次规模与超时预算的**约束测试**（防止静默回归）。

## 本文件防的是什么

真实链路上细纲**连续 3 次超时**（每次 300s，共 1135s 全废），
根因是批次 15 章超过了 300s 超时上限。

实测单批成本（book 16 / MiMo v2.5 / `reasoning_effort=high`）：

    1 章 →  48.2s      3 章 → 115.8s      6 章 → 147.9s

线性拟合 `耗时 ≈ 39.9s + 19.2s × 章数` → **15 章预测 328s > 300s**，
与实测三连败吻合。修为 5 章（预测 136s，占超时 45%）。

## 为什么要有测试而不是只改常量

`detail_batch_size` 是个"看起来调大点更快"的参数 ——
很容易有人为了"少几批"把它调回 15，然后重新踩同一个坑。
本测试把**约束关系**写死：

    批次章数 × 每章实测耗时 + 固定开销  <  超时上限 × 安全系数

⚠️ 换模型 / 换 effort / 改超时上限后，**必须重测每章耗时**再调这两个值
（脚本 `scripts/diag_batch_model.py`）。本测试的实测常数写在下面，
重测后同步更新。
"""

from __future__ import annotations

import pytest

from storyforge.config import get_settings

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# 实测常数（MiMo v2.5 + reasoning_effort=high，book 16 卷 1）
# --------------------------------------------------------------------------- #

#: 固定开销（秒）：prompt 组装 + 首 token 延迟
FIXED_OVERHEAD_S = 39.9
#: 每章增量（秒）：实测拟合值（1/3/6 章三个点）
SECONDS_PER_CHAPTER = 19.2
#: 安全系数：批次耗时**不应超过**超时上限的这个比例。
#:
#: 取 0.6 的理由：超时是**整批作废**（失败批次不落盘），
#: 一次超时白烧整批 token；且实际耗时受服务端负载影响会波动。
#: 留 40% 余量是「不把预算用满」的工程惯例。
SAFE_UTILIZATION = 0.6


def _predicted_seconds(chapters: int) -> float:
    return FIXED_OVERHEAD_S + SECONDS_PER_CHAPTER * chapters


def test_detail_batch_fits_within_timeout() -> None:
    """★ 细纲一批的**预测耗时**必须显著小于超时上限。

    这是本轮真实缺陷（3 连超时）的直接回归测试。
    """
    s = get_settings()
    batch = s.pipeline.detail_batch_size
    timeout = s.llm.default_timeout_s

    predicted = _predicted_seconds(batch)
    limit = timeout * SAFE_UTILIZATION

    assert predicted < limit, (
        f"细纲批次 {batch} 章预测耗时 {predicted:.0f}s，"
        f"超过超时上限 {timeout:.0f}s 的 {SAFE_UTILIZATION:.0%}（={limit:.0f}s）。\n"
        f"实测 15 章 = 328s > 300s → 连续 3 次超时、整批作废。\n"
        f"改小 `PipelineSettings.detail_batch_size`，"
        f"或按 scripts/diag_batch_model.py 重测后调整。"
    )


#: 立这条批大小约束**当时**的超时值（2026-09-23，MiMo v2.5）。
#:
#: ★ 刻意写成**字面常量**，不读 `get_settings().llm.default_timeout_s`：
#: 后者会随配置一起变，于是"把超时调大"就能让反向验证自动通过 ——
#: 那正是本项目反复禁止的**"自适应的测试等于没测"**。
TIMEOUT_WHEN_BATCH_CONSTRAINT_WAS_SET = 300.0


def test_the_old_value_15_would_have_failed_at_the_then_timeout() -> None:
    """★ 反向验证：在**当时的 300s 超时**下，15 章必须是被拦下的。

    防的是"把阈值调到刚好放过当前值"—— 若 15 在 300s 下也能通过，
    说明这条约束根本没在守任何东西。

    ## 为什么断言的是历史常量而不是当前超时

    2026-09-26 实测吞吐只有 13.7 tok/s，而 `max_output_tokens_cap=32768`
    需要 ~2392s —— 旧的 300s 超时让**输出上限物理不可达**，故超时提到 2400s。

    副效应：15 章（328s）在 2400s 下**不再**失败。这不是"放宽约束"，
    而是**批大小约束与超时约束解耦了** ——
      · 旧理由（"15 章会超时"）**已不成立**
      · 仍成立的理由是"超时 = 整批作废，小批限制白烧的 token 量"
    故批大小仍取 5，本测试改为钉住**历史事实**，防止有人用"调大超时"
    把这条反向验证悄悄变成恒真。
    """
    predicted_15 = _predicted_seconds(15)
    assert predicted_15 > TIMEOUT_WHEN_BATCH_CONSTRAINT_WAS_SET, (
        f"按实测模型 15 章需 {predicted_15:.0f}s，并未超过当年的 "
        f"{TIMEOUT_WHEN_BATCH_CONSTRAINT_WAS_SET:.0f}s —— 与真实观测到的三连超时矛盾。"
        f"实测常数（FIXED_OVERHEAD_S / SECONDS_PER_CHAPTER）可能已过期，"
        f"请重跑 scripts/diag_batch_model.py 校准。"
    )


def test_batch_constraint_is_not_vacuous_at_the_current_timeout() -> None:
    """★ 当前超时下，**某个**批大小仍必须会失败（约束不能退化恒真）。

    纯粹的反向验证（"15 必须失败"）在超时调大后必然失效，
    所以要另外钉一条**与超时无关**的：约束在当前配置下依然有区分力。
    """
    s = get_settings()
    batch = s.pipeline.detail_batch_size
    limit = s.llm.default_timeout_s * SAFE_UTILIZATION

    # 当前配置必须通过；且存在一个更大的批大小会失败
    assert _predicted_seconds(batch) < limit, "当前批大小已超限"
    breaking = next(
        n for n in range(batch, 200) if _predicted_seconds(n) >= limit
    )
    assert breaking > batch, (
        f"批大小约束已退化：连 200 章都不会失败（limit={limit:.0f}s），"
        f"说明超时被调得过大、这条约束不再守任何东西"
    )



def test_batch_size_is_at_least_one() -> None:
    """批次不得为 0 或负数（否则不生成任何章）。"""
    assert get_settings().pipeline.detail_batch_size >= 1


# --------------------------------------------------------------------------- #
# ★ 超时 与 输出上限 必须相容（S0 真实取证暴露的配置矛盾）
# --------------------------------------------------------------------------- #

#: 实测吞吐（2026-09-26，MiMo v2.6-flash，真实 S0 单章）：
#: 1008 completion token / 73.7s = 13.7 tok/s。
#: ⚠️ 换模型 / 换 provider 后**必须重测**再调这个常数（同本文件顶部的纪律）。
MEASURED_TOKENS_PER_SECOND = 13.7


def test_output_cap_is_reachable_within_the_timeout() -> None:
    """★★ `max_output_tokens_cap` 必须能在 `default_timeout_s` 内**真的产出**。

    这是 S0 真实取证暴露的配置矛盾：旧值 `timeout=300s` 在实测 13.7 tok/s 下
    只够 ~4,110 token，而 `cap=32768` 需要 **2,392s** ——
    **cap 物理不可达**。症状是"长章/细纲批次永远超时"，很容易被误读成
    "模型太慢"，而根因是两条配置互相打架。

    判据：`cap ÷ 实测吞吐 ≤ timeout`。
    """
    from storyforge.infra.llm.client import ClientConfig

    s = get_settings()
    timeout = s.llm.default_timeout_s
    cap = ClientConfig().max_output_tokens_cap

    needed = cap / MEASURED_TOKENS_PER_SECOND
    assert needed <= timeout, (
        f"输出上限 {cap} token 在实测 {MEASURED_TOKENS_PER_SECOND} tok/s 下需要 "
        f"{needed:.0f}s，但超时只有 {timeout:.0f}s。\n"
        f"→ `max_output_tokens_cap` **不可达**，长章会以超时收场。\n"
        f"修法：提高 `LLMSettings.default_timeout_s`（与 ClientConfig.timeout_s 同步），"
        f"或调低 cap —— 但**不要**只改一边。\n"
        f"若换了模型/provider，请先重测吞吐并更新 MEASURED_TOKENS_PER_SECOND。"
    )


def test_the_old_timeout_300s_would_have_failed() -> None:
    """★ 反向验证：**旧值 300s 必须是被本约束拦下的**。

    防的是"把阈值调到刚好放过当前值"—— 若 300s 也能通过，
    说明这条约束根本没在守任何东西（本项目已栽过三次同类问题）。
    """
    from storyforge.infra.llm.client import ClientConfig

    cap = ClientConfig().max_output_tokens_cap
    needed = cap / MEASURED_TOKENS_PER_SECOND
    assert needed > 300.0, (
        f"按 13.7 tok/s，{cap} token 只需 {needed:.0f}s，并未超过旧超时 300s —— "
        f"与 S0 实测到的超时矛盾。实测常数可能已过期，请重测。"
    )

