"""多书测试的**失败分类**测试（`scripts/multibook_e2e.py::_classify`）。

## 为什么这个分类值得专门的测试

多书测试的全部价值在于**按类别数书数**，再按 `D-16` 判据决策：

    ≥3 本同类失败 → 通用缺陷，必须修链路
    1 本          → 单书特例，只记录，不得放宽阈值

**分类错了，判据就跟着错。** 实测发生过两次：

1. **广匹配吃掉窄匹配（死代码）**：`("卷", "volume:other")` 排在骨架两条之前，
   而骨架消息里含「本**卷**」二字
   → `未落实本卷台阶指定的新势力` 永远命中不到 → 书 13/15 的**骨架**失败
     被误标成 `volume:other`，看起来像台阶问题。

2. **靠类别名匹配，而真实消息里没有那个词**：原先只登记 `("细纲", "detail:lint")`，
   但细纲的失败消息实际是「地点跳变…」「本批应输出 15 章…」「场景数 3 < 4」
   —— **一个都不含「细纲」** → 细纲整类掉进 `other`，等于没有分类。

因此本文件的断言全部使用**真实消息原文**（取自 `doc/research/_diag_*.json`
与历史 lint 输出），而不是我编的示例串。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.multibook_e2e import _classify  # noqa: E402


# --------------------------------------------------------------------------- #
# 骨架消息必须归到 skeleton:*（原先被 volume:other 吞掉）
# --------------------------------------------------------------------------- #


def test_skeleton_new_factions_not_swallowed_by_volume() -> None:
    """★ 回归：消息里有「本卷」二字，但它是**骨架**失败，不是台阶失败。

    这是「广匹配排在窄匹配之前」造成的死代码（实测 bug）。
    """
    msg = ("骨架未落实本卷台阶指定的新势力：['旧派（主张严格恪守现有手册，"
           "反对任何调查或改变）'] —— 台阶规定了扩张")
    assert _classify(msg) == "skeleton:new_factions"


def test_skeleton_new_locations_not_swallowed_by_volume() -> None:
    msg = "骨架的故事线/段落未提及本卷新地图：['城市边缘的废弃印刷厂']"
    assert _classify(msg) == "skeleton:new_locations"


@pytest.mark.parametrize("msg,expect", [
    ("故事线只有 4 条（要求 ≥6）", "skeleton:arcs"),
    ("对立线只有 1 条（要求 ≥2）", "skeleton:opposition"),
    ("关系线只有 0 条（要求 ≥2）", "skeleton:relationship"),
    ("主要角色只有 1 个（要求 ≥3）", "skeleton:characters"),
    ("段落目标有 2 处逐字重复", "skeleton:segments"),
])
def test_skeleton_structural_messages(msg, expect) -> None:
    assert _classify(msg) == expect


# --------------------------------------------------------------------------- #
# 台阶消息
# --------------------------------------------------------------------------- #


def test_volume_milestone_gap_is_unclassified_not_other() -> None:
    """里程碑真空归 `volume:unclassified`（**看得见的未细分类**）。

    用 `unclassified` 而不是伪装成某个具体类别 —— 这样报表里能一眼看出
    「这条还没被细分」，而不是误以为已经归好类了。
    """
    msg = "第 4 卷存在 30 章的里程碑真空（上限 25）—— 旧项目书 23 实测 202 章真空"
    assert _classify(msg) == "volume:unclassified"


def test_volume_share_skew_is_unclassified() -> None:
    msg = "第 6 卷 110 章，占全书 14%，超出 [15%, 60%]（目标 800 章）"
    assert _classify(msg) == "volume:unclassified"


@pytest.mark.parametrize("msg,expect", [
    ("第 1 卷只开 1 个新地图（要求 ≥2）", "volume:new_locations"),
    ("第 2 卷只登场 1 个新势力（要求 ≥2）", "volume:new_factions"),
])
def test_volume_ladder_messages(msg, expect) -> None:
    assert _classify(msg) == expect


# --------------------------------------------------------------------------- #
# 圣经消息
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("msg,expect", [
    ("圣经过薄（合计 866 字 < 1200）—— 长篇需要足够的设定密度", "bible:too_thin"),
    ("世界观一节过短（30 字 < 60）—— 需要交代世界结构与当前局势", "bible:worldview"),
    ("硬规则只有 3 条（要求 ≥6）", "bible:rules"),
    ("术语对照只有 0 条（要求 ≥4）", "bible:terms"),
    ("rules 里有 1 条是**其他字段的内容**（以字段名开头）", "bible:misnest"),
    ("模型输出无法解析：Invalid JSON: expected ':'", "bible:parse"),
])
def test_bible_messages(msg, expect) -> None:
    assert _classify(msg) == expect


def test_bible_misnest_label_is_legacy_only() -> None:
    """★ `BibleOut` 已改为**自动归位**，新跑的书不可能再落到 `bible:misnest`。

    但旧 `_diag_*.json` 里存着这条消息 —— 映射必须保留，否则历史报表
    会整类掉进 `other`，"前后对比"就没有基线了（这正是修 `_classify` 的原因）。
    """
    from storyforge.infra.llm.schemas import BibleOut  # noqa: PLC0415

    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="",
        rules=["规则一：不可违反", "era：永恒黄昏时代"],
        terms=["占位——非空"],
    )
    # 归位成功 → 不再报错、不再产生 misnest 消息
    assert b.era == "永恒黄昏时代"
    assert b.rules == ["规则一：不可违反"]


# --------------------------------------------------------------------------- #
# ★ 细纲消息：真实措辞里没有「细纲」二字
# --------------------------------------------------------------------------- #


def test_detail_continuity_message_has_no_word_detail() -> None:
    """★ 回归：这条消息里**没有「细纲」**，靠类别名匹配会掉进 `other`。

    这是"匹配词按类别名写、而真实消息用具体描述"造成的整类丢失。
    """
    msg = "第2章：地点跳变：本章结束于「青岚宗杂役弟子通铺」，下一章开始于「青岚宗外门杂役区」"
    assert "细纲" not in msg, "前提：真实消息确实不含「细纲」"
    assert _classify(msg) == "detail:continuity"


def test_detail_batch_size_message() -> None:
    msg = "本批应输出 15 章（1~15），实际 12 章 —— 章号由代码钉定，数量不符无法对齐"
    assert _classify(msg) == "detail:batch_size"


def test_detail_scene_count_message() -> None:
    assert _classify("第5章场景数 3 < 4") == "detail:scenes"


def test_detail_title_duplicate_message() -> None:
    assert _classify("章标题与第3章逐字重复：「深夜的敲门声」") == "detail:title_dup"


# --------------------------------------------------------------------------- #
# 兜底
# --------------------------------------------------------------------------- #


def test_unknown_message_falls_back_to_other() -> None:
    assert _classify("完全无法归类的消息") == "other"


def test_empty_and_none_are_safe() -> None:
    assert _classify("") == "other"
    assert _classify(None) == "other"  # type: ignore[arg-type]


def test_longer_keyword_wins_over_shorter() -> None:
    """★ 核心不变量：**更具体的关键词必须优先命中**。

    不依赖人肉维护顺序 —— `_classify` 按关键词长度降序排，
    这条断言同时保护"将来加规则不需要人工判断插在哪一行"这个性质。
    """
    # 同时含「本卷」（宽）与「未提及本卷新地图」（窄）→ 应命中窄的
    msg = "骨架的故事线/段落未提及本卷新地图：['X']"
    assert _classify(msg) == "skeleton:new_locations"
    # 同时含「卷」（最宽）与「新势力」（窄）→ 应命中窄的
    assert _classify("第 1 卷只开 1 个新势力") == "volume:new_factions"
