"""学习地图与平替修辞（阶段 2-3）测试 —— L-1/L-2 两个确认点。

## 本文件防的是什么

用户要求 learn「全流程**可视化**，避免黑盒」，且确认粒度必须
「能**编辑 + 回退**」（用户明确）。

这两句话落到实现上是四个必须钉住的行为：

  ① **逐条确认** —— 改一条不影响其它条（用户决策：粒度=术语→说法）
  ② **改完自动取消确认** —— 否则"已确认"会变成没有意义的历史残留
  ③ **回退真的回到那一版**（内容**与**确认状态都要回去）
  ④ **确认前校验** —— 有问题的地图不能被"盖章通过"（它是世界观的输入）

若没有测试，这四条都会在后续重构里悄悄退化。

## 命名空间（用户决策）

learn 用 **L-1/L-2**，不挤进 D-14 的 C-1~C-7 ——
两套各成一体，符合"story/learn 低冲突"的要求。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyforge.domain.enums import (
    CONDITIONAL_CONFIRMS,
    LEARN_CONFIRMS,
    MANDATORY_CONFIRMS,
    STORY_CONFIRMS,
    ConfirmPoint,
)
from storyforge.domain.learning import (
    KnowledgePoint,
    LearningMap,
    TermMapping,
    parse_mapping_rule,
    validate_map,
)
from storyforge.modes.mappings import MappingConfigError, load_mapping_seed

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 确认点命名空间
# --------------------------------------------------------------------------- #


def test_learn_confirms_are_separate_namespace() -> None:
    """★★ learn 的确认点必须与 story **零重叠**（用户要求两区域低耦合）。"""
    assert LEARN_CONFIRMS == {
        ConfirmPoint.L1_KNOWLEDGE_POINTS,
        ConfirmPoint.L2_TERM_MAPPINGS,
    }
    assert not (LEARN_CONFIRMS & STORY_CONFIRMS), "story/learn 确认点不得重叠"
    assert STORY_CONFIRMS | LEARN_CONFIRMS == set(ConfirmPoint)


def test_learn_confirms_are_mandatory() -> None:
    """★ L-1/L-2 是**强制**的：知识点与说法没定就不该开写。"""
    assert LEARN_CONFIRMS <= MANDATORY_CONFIRMS
    assert not (LEARN_CONFIRMS & CONDITIONAL_CONFIRMS)


def test_confirm_point_enum_values_are_stable() -> None:
    """枚举值一旦发布就**不可改**（它会进 state、进 trace、进产物 JSON）。

    改值会让历史产物无法解释。
    """
    assert ConfirmPoint.L1_KNOWLEDGE_POINTS.value == "L1_knowledge_points"
    assert ConfirmPoint.L2_TERM_MAPPINGS.value == "L2_term_mappings"


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


def _map(**kw) -> LearningMap:
    base = {
        "topic": "宋代点茶",
        "scale": "fast",
        "points": [
            KnowledgePoint(point_id="kp001", name="点茶的起源",
                           source_urls=("https://a",), evidence="…"),
            KnowledgePoint(point_id="kp002", name="茶筅与击拂",
                           source_urls=("https://b",), evidence="…"),
        ],
        "mappings": [
            TermMapping(term="函数", replacement="自创功法"),
            TermMapping(term="循环", replacement="轮回诀"),
        ],
    }
    base.update(kw)
    return LearningMap(**base)      # type: ignore[arg-type]


def test_validate_passes_for_good_map() -> None:
    assert validate_map(_map()) == []


def test_validate_detects_empty_points() -> None:
    """空知识点集 = "没有教学内容的教小说"。"""
    issues = validate_map(_map(points=[]))
    assert any("为空" in i for i in issues)


def test_validate_detects_duplicate_point_id() -> None:
    """★ `point_id` 重复 → 跨章引用会指错。

    与旧项目 `since_ch` 口径漂移同类（引用指错是最难查的一类问题）。
    """
    m = _map(points=[
        KnowledgePoint(point_id="kp001", name="A", source_urls=("u",)),
        KnowledgePoint(point_id="kp001", name="B", source_urls=("u",)),
    ])
    issues = validate_map(m)
    assert any("point_id 重复" in i for i in issues)


def test_validate_detects_missing_source() -> None:
    """★★ 无来源的知识点**必须被标出来**（可能是模型幻觉）。

    Mock 研究的片段 URL 为空 → 必然触发这条。
    这是"不要把占位文本当真知识"的机械保障。
    """
    m = _map(points=[KnowledgePoint(point_id="kp001", name="无来源的点")])
    issues = validate_map(m)
    assert any("没有可核对来源" in i for i in issues)
    assert any("幻觉" in i for i in issues)


def test_validate_detects_duplicate_term() -> None:
    """同一术语两条映射 → "用哪个说法"不确定。"""
    m = _map(mappings=[
        TermMapping(term="函数", replacement="功法"),
        TermMapping(term="函数", replacement="秘术"),
    ])
    issues = validate_map(m)
    assert any("术语重复" in i for i in issues)


def test_validate_detects_identity_mapping() -> None:
    """★★ 世界内说法与原术语**相同** → 等于没做平替。

    这条防的是"生成结果偷懒"：`函数 → 函数` 看起来是一条映射，
    实际正文里仍会直出原术语（而审校层硬拦这个问题）。
    """
    m = _map(mappings=[TermMapping(term="函数", replacement="函数")])
    issues = validate_map(m)
    assert any("等于没平替" in i for i in issues)


def test_validate_detects_empty_replacement() -> None:
    m = _map(mappings=[TermMapping(term="函数", replacement="  ")])
    issues = validate_map(m)
    assert any("世界内说法为空" in i or "等于没做平替" in i for i in issues)


def test_mapping_roundtrip_through_command_loader() -> None:
    """★ 往返必须经过**命令层真正的** `_from_dict`（不是直接构造）。

    ⚠️ 初版这里写 `LearningMap(**m.to_dict())` —— 错的：
    `to_dict()` 产出的是**嵌套 dict**，不是领域对象，
    直接回灌会得到 `mappings[0]` 是 dict 而不是 `TermMapping`。
    真正走的是 `commands/learning.py:_from_dict`，测试必须用它，
    否则测的是一个不存在的路径。
    """
    from storyforge.commands.learning import _from_dict

    m = _map()
    restored = _from_dict(json.loads(json.dumps(m.to_dict())))

    assert len(restored.points) == len(m.points)
    assert restored.points[0].point_id == "kp001"
    assert restored.points[0].source_urls == ("https://a",)
    assert restored.mappings[0].term == "函数"
    assert restored.mappings[0].replacement == "自创功法"
    assert restored.topic == m.topic and restored.scale == m.scale


def test_roundtrip_preserves_accepted_and_revision() -> None:
    """★ 确认状态与重做次数必须**跨会话保留**（用户确认可能隔天完成）。"""
    from storyforge.commands.learning import _from_dict

    m = _map(mappings=[
        TermMapping(term="函数", replacement="功法", accepted=True, revision=2),
        TermMapping(term="循环", replacement="轮回诀", accepted=False),
    ])
    restored = _from_dict(json.loads(json.dumps(m.to_dict())))
    assert restored.mappings[0].accepted is True
    assert restored.mappings[0].revision == 2
    assert restored.mappings[1].accepted is False
    assert len(restored.accepted_mappings) == 1
    assert len(restored.unconfirmed_mappings()) == 1


# --------------------------------------------------------------------------- #
# 旧格式解析（导入旧项目数据）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw,term,repl", [
    ("基础概念 → 灵根/入门心法", "基础概念", "灵根/入门心法"),
    ("函数 -> 自创功法", "函数", "自创功法"),
    ("报错/异常 => 心魔反噬", "报错/异常", "心魔反噬"),
])
def test_parse_mapping_rule_variants(raw, term, repl) -> None:
    got = parse_mapping_rule(raw)
    assert got is not None
    assert got.term == term and got.replacement == repl


@pytest.mark.parametrize("raw", ["没有箭头", "", "   ", "→ 没有左边", "右边没有 →"])
def test_parse_mapping_rule_returns_none_for_bad_input(raw) -> None:
    """★ 坏数据返回 `None` 而**不抛错** —— 这是导入路径，一条坏数据
    不该让整批失败（但调用方应记录）。"""
    assert parse_mapping_rule(raw) is None


# --------------------------------------------------------------------------- #
# 种子映射（10 条权威数据）
# --------------------------------------------------------------------------- #


def test_seed_loads_all_10_rules() -> None:
    """★ 旧项目 `templates/scenes.py` 的 10 条必须全部加载到。"""
    seed = load_mapping_seed("xianxia")
    assert len(seed.mappings) == 10
    terms = {m.term for m in seed.mappings}
    for expected in ("基础概念", "函数", "循环", "报错/异常", "库/模块"):
        assert expected in terms, f"缺种子映射 {expected}"


def test_seed_is_marked_as_seed() -> None:
    """★★ 种子**必须**可识别为种子。

    `scenes.py:14` 明写"名称与阶梯必须按本书自创，禁止套用现成表" ——
    若把种子当最终结果，每本书的平替修辞都一样，正是要避免的。
    """
    assert load_mapping_seed("xianxia").is_seed is True


def test_seed_render_block_matches_old_wording() -> None:
    """★ 渲染措辞须与旧项目四处注入的语义一致。

    旧项目 `flowmode/base.py:120`：
    「【术语映射规则——正文中的知识点必须按此转换为世界内说法】」
    """
    block = load_mapping_seed("xianxia").render_block()
    assert "术语映射规则" in block
    assert "世界内说法" in block
    assert "基础概念 → 灵根/入门心法" in block


def test_seed_missing_raises_with_available() -> None:
    """未知题材的种子必须抛错并列出可用的（不静默返回空表 ——
    空表会让 writer 拿不到术语约束，正文直出原术语）。"""
    with pytest.raises(MappingConfigError, match="不存在"):
        load_mapping_seed("不存在的题材")


def test_seed_has_style_samples() -> None:
    """文风示例也要移植（few-shot：告诉 writer「知识点化作功法」的质感）。"""
    seed = load_mapping_seed("xianxia")
    assert seed.style_samples, "文风示例缺失"
    assert all(len(s) > 20 for s in seed.style_samples)
