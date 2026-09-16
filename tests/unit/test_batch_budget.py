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


def test_the_old_value_15_would_have_failed() -> None:
    """★ 反向验证：**旧值 15 必须是被本测试拦下的**。

    防的是"把阈值调到刚好放过当前值" —— 若 15 也能通过，
    说明这条测试根本没在守任何东西（阈值被设得太松）。
    """
    s = get_settings()
    timeout = s.llm.default_timeout_s
    predicted_15 = _predicted_seconds(15)
    assert predicted_15 > timeout, (
        f"按实测模型 15 章只需 {predicted_15:.0f}s，"
        f"并未超过超时 {timeout:.0f}s —— 与真实观测到的三连超时矛盾。"
        f"实测常数（FIXED_OVERHEAD_S / SECONDS_PER_CHAPTER）可能已过期，"
        f"请重跑 scripts/diag_batch_model.py 校准。"
    )


def test_batch_size_is_at_least_one() -> None:
    """批次不得为 0 或负数（否则不生成任何章）。"""
    assert get_settings().pipeline.detail_batch_size >= 1
