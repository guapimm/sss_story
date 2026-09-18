"""learn 规划节点与契约（阶段 2-3 收尾：LLM 生成学习地图 / 平替修辞）。

## 本文件防的是什么

阶段 2-3 的 `gen` 命令此前用**种子**（旧项目 10 条）+ 确定性知识点切分 ——
那是"离线可跑通"的权宜。真正的 learn 需要 **LLM 生成**：

    研究资料 → 学习地图（模块 + 知识点）
    地图     → 缺口自检（找盲区）
    知识点   → 平替修辞（术语 → 世界内说法）

三条都由本文件的被测对象承担。

## ★ 最重要的两条断言（都是实测教训）

1. **prompt 必须带 JSON 骨架** —— 缺陷 11 的根因就是"抽取节点漏登记到
   `STRUCTURED_NODES`，长期无骨架而契约全绿"。故本文件直接断言
   三个 prompt 常量都含 ```json 块（`test_prompt_skeletons.py` 也覆盖，
   此处就近再钉一次，防"登记被删）
2. **形状归一** —— `GapCheckOut` 必须接受裸列表与中英混合键
   （与抽取侧同一原则：**归一，不拒绝**）
"""

from __future__ import annotations

import re

import pytest

from storyforge.infra.llm.learning_schemas import (
    GAP_CHECK_SYSTEM,
    LEARNING_MAP_SYSTEM,
    MAPPING_SYSTEM,
    GapCheckOut,
    LearningMapOut,
    TermMappingBatchOut,
)
from storyforge.graph.nodes.learning import SCALE_PROFILE

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# prompt 骨架（缺陷 11 的教训）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,prompt", [
    ("learning_map", LEARNING_MAP_SYSTEM),
    ("gap_check", GAP_CHECK_SYSTEM),
    ("term_mapping", MAPPING_SYSTEM),
])
def test_prompt_has_json_skeleton(name: str, prompt: str) -> None:
    """★★ 三个 prompt 都必须含 ```json 骨架。

    这是缺陷 11 的直接教训：**prompt 只写散文 → 模型自造字段名 →
    整轮失败**。`STRUCTURED_NODES` 已登记这三个节点，
    此处就近再断言一次（登记被误删时这条仍会响）。
    """
    blocks = re.findall(r"```json\s*(.+?)```", prompt, re.S)
    assert blocks, f"{name} 的 prompt 没有 JSON 骨架"


def test_map_prompt_declares_hard_limits() -> None:
    """★ 学习地图的 prompt 要写明**防膨胀约束**（旧项目实测出来的）。

    旧项目 `_MapFiller` 的硬限制：`description ≤40 字`、
    每模块 `knowledge_points ≤6`、`summary ≤30 字`。
    不写清楚会让模型给出巨型模块 —— 而每个知识点后面要写成正文剧情。
    """
    assert "≤40 字" in LEARNING_MAP_SYSTEM or "40 字" in LEARNING_MAP_SYSTEM
    assert "6 个" in LEARNING_MAP_SYSTEM, "未声明每模块知识点上限"
    assert "只依据给定资料" in LEARNING_MAP_SYSTEM, "未禁止编造"


def test_mapping_prompt_bans_identity_replacement() -> None:
    """★★ 平替修辞 prompt 必须禁止"说法与原术语相同"。

    这是"等于没做平替"的源头（且 `validate_map` 也会拒绝它）。
    """
    assert "必须与原术语不同" in MAPPING_SYSTEM


def test_mapping_prompt_mentions_rationale() -> None:
    """★ `rationale` 是给用户判断用的 —— 用户决策 L-2 要**逐条确认**。

    没有理由，用户只能盲选（"为什么把 Token 叫指令刻度"）。
    """
    assert "rationale" in MAPPING_SYSTEM
    assert "供用户判断" in MAPPING_SYSTEM


def test_gap_prompt_allows_empty_result() -> None:
    """★ 缺口自检必须允许"没有盲区"（否则模型会硬凑）。

    prompt 里明确要求覆盖完整时返回空数组。
    """
    assert '{"gaps": []}' in GAP_CHECK_SYSTEM or "空数组" in GAP_CHECK_SYSTEM


# --------------------------------------------------------------------------- #
# 分档规模（与 2-2 的研究目标配对）
# --------------------------------------------------------------------------- #


def test_scale_profile_matches_old_project() -> None:
    """★ 分档规模逐字来自旧项目 `researcher.py:28-33` 的 `RESEARCH_PROFILE`。"""
    assert SCALE_PROFILE["fast"]["points_per_module"] == 4
    assert SCALE_PROFILE["standard"]["points_per_module"] == 6
    assert SCALE_PROFILE["grand"]["points_per_module"] == 8
    assert SCALE_PROFILE["ultra"]["points_per_module"] == 10
    assert SCALE_PROFILE["standard"]["modules"] == "3~5"


def test_scale_profile_covers_research_target() -> None:
    """★★ 地图规模必须**容得下**研究目标（阶段 2-2 的 `points_per_scale`）。

    两者口径不一致会出现"搜了很多但地图很小"（资料浪费）
    或"地图比资料大"（必然编造）。
    """
    from storyforge.config import get_settings

    targets = get_settings().research.points_per_scale
    for scale, target in targets.items():
        prof = SCALE_PROFILE[scale]
        lo, hi = str(prof["modules"]).split("~")
        capacity = int(hi) * int(prof["points_per_module"])
        assert capacity >= target, (
            f"{scale} 档：地图最多容 {capacity} 个知识点，"
            f"但研究目标是 {target} 个 —— 装不下"
        )


# --------------------------------------------------------------------------- #
# 形状归一（对输入宽容）
# --------------------------------------------------------------------------- #


def test_gap_check_accepts_bare_list() -> None:
    """★ 模型可能直接给**裸列表**（实测见过的形状）→ 必须归一。"""
    out = GapCheckOut.model_validate(["盲区A", "盲区B"])
    assert out.gaps == ["盲区A", "盲区B"]


def test_gap_check_accepts_single_string() -> None:
    """★ `{"gaps": "单个字符串"}` → 包成单元素列表（不拒）。"""
    out = GapCheckOut.model_validate({"gaps": "只有一个盲区"})
    assert out.gaps == ["只有一个盲区"]


def test_gap_check_accepts_chinese_key() -> None:
    """★ 中文键名（`盲区`）也要接受 —— 模型常给中文键。"""
    out = GapCheckOut.model_validate({"盲区": ["方向1"]})
    assert out.gaps == ["方向1"]


def test_gap_check_accepts_empty() -> None:
    """覆盖完整是**合法结果**（不强制有盲区）。"""
    assert GapCheckOut.model_validate({"gaps": []}).gaps == []


def test_gap_check_normalizes_scalar_gaps() -> None:
    """非列表非字符串的 `gaps`（如数字）→ 归一为单元素字符串列表。

    ★ 目标是"**不炸**且不丢信息"：字段类型是 `list[str]`，
    所以 `123` 应变成 `"123"`（而不是抛 `Input should be a valid list`）。

    ⚠️ 初版这条测试写的是 `out.gaps == [123] or out.gaps == []` ——
    **断言错了**（字段是 `list[str]`，`[123]` 永远不可能出现）。
    实测发现后改为断言真实行为。
    """
    out = GapCheckOut.model_validate({"gaps": 123})
    assert out.gaps == ["123"]


# --------------------------------------------------------------------------- #
# 学习地图校验（空地图必须被拒）
# --------------------------------------------------------------------------- #


def _map_payload(**kw) -> dict:
    base = {
        "topic_summary": "总述",
        "modules": [{
            "name": "基础",
            "knowledge_points": [{"name": "知识点1", "summary": "说明"}],
        }],
    }
    base.update(kw)
    return base


def test_map_accepts_valid_payload() -> None:
    out = LearningMapOut.model_validate(_map_payload())
    assert out.point_count == 1


def test_map_rejects_no_modules() -> None:
    """★★ 空地图必须被拒 —— "没有内容的教小说"是无效输入。

    拒它会让 client 重试（可能拿到有效结果）；
    接受它会让下游拿到空地图，然后**在更远的地方**才暴露问题。
    """
    with pytest.raises(ValueError, match="至少要有一个模块"):
        LearningMapOut.model_validate({"modules": []})


def test_map_rejects_modules_without_points() -> None:
    """★★ 有模块但**没有知识点**同样是空壳 → 必须被拒。"""
    with pytest.raises(ValueError, match="空壳"):
        LearningMapOut.model_validate({
            "modules": [{"name": "空模块", "knowledge_points": []}]
        })


def test_map_point_count_sums_all_modules() -> None:
    out = LearningMapOut.model_validate({
        "modules": [
            {"name": "A", "knowledge_points": [{"name": "a1"}, {"name": "a2"}]},
            {"name": "B", "knowledge_points": [{"name": "b1"}]},
        ]
    })
    assert out.point_count == 3


# --------------------------------------------------------------------------- #
# 平替修辞契约
# --------------------------------------------------------------------------- #


def test_term_mapping_batch_parses() -> None:
    out = TermMappingBatchOut.model_validate({
        "mappings": [
            {"term": "函数", "replacement": "自创功法", "rationale": "可复用"},
            {"term": "循环", "replacement": "轮回诀"},
        ]
    })
    assert len(out.mappings) == 2
    assert out.mappings[0].rationale == "可复用"
    assert out.mappings[1].rationale == ""      # 缺省不是必填


def test_term_mapping_empty_is_allowed_by_schema() -> None:
    """schema 层允许空（**校验留给 `validate_map`**）。

    分工：schema 管"形状对不对"，`validate_map` 管"内容够不够好"。
    在 schema 层硬拦内容质量问题会整轮作废（本项目已吃过这个亏：
    缺陷 9「质量偏好被放在 schema 层硬拦」）。
    """
    assert TermMappingBatchOut.model_validate({"mappings": []}).mappings == []
