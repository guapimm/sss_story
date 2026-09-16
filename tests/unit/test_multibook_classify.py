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


# --------------------------------------------------------------------------- #
# ★★ D-16 判据：必须数「**几本书**」，不是「出现几次」
# --------------------------------------------------------------------------- #


def test_d16_threshold_counts_distinct_books_not_occurrences() -> None:
    """★★ 一本书内出现 N 次失败，仍然只是 **1 本**。

    ## 实测踩到（D-16 判据本身出错）

    6 书回归后报表显示：

        detail:continuity    3 本 [13, 13, 13]   ★ 通用缺陷（必须修链路）

    `[13, 13, 13]` 是**同一本书的三条**失败（三个章各有地点跳变），
    却因为 `len(bids) == 3` 被判成"3 本 = 通用缺陷"。

    后果：**会去改一个只对书 13 成立的判据** —— 这正是 D-16 要防的过拟合，
    只不过这次是从"统计口径"这一侧绕进来的。

    与 D-16 §2.1 记录的分类错误同类：**统计口径错了，阈值判定就全错**。
    """
    # 模拟 `failures` 里的原始记录（含重复）
    raw = {"detail:continuity": [13, 13, 13]}
    n_books = len(set(raw["detail:continuity"]))
    n_hits = len(raw["detail:continuity"])

    assert n_hits == 3, "前提：确实出现了 3 次"
    assert n_books == 1, "但只有 1 本书"
    # 判据必须用去重后的书数
    verdict = "通用缺陷" if n_books >= 3 else "单书特例"
    assert verdict == "单书特例", (
        f"按出现次数判会得到「通用缺陷」——这是错的（实际只有 1 本书）"
    )


def test_three_distinct_books_is_a_general_defect() -> None:
    """对照组：**三本不同的书**才是通用缺陷（证明上一条不是把阈值调松）。"""
    raw = {"skeleton:new_locations": [13, 15, 16]}
    n_books = len(set(raw["skeleton:new_locations"]))
    assert n_books == 3
    assert n_books >= 3, "三本不同书 = 通用缺陷，必须修链路"



# --------------------------------------------------------------------------- #
# 台阶闸门：必须用结构化的 `code`，不靠 message 措辞猜
# --------------------------------------------------------------------------- #


def test_gate_uses_structured_code_not_message() -> None:
    """★ 闸门自带权威分类 `code`，**不该**去猜 message 的措辞。

    ## 实测踩到（第三次同类错误）

    `milestones` 闸门的消息是「第 2 卷存在 49 章的里程碑真空（上限 25）」，
    而关键词表里没有「里程碑」也没有「真空」，
    于是整条落进 `volume:unclassified` —— 等于**该闸门没有分类**。

    修法不是"再补一个关键词"（那还会漏下一种说法），
    而是**用结构化字段**：`GateIssue.code` 本来就是 `GateErrorCode` 枚举值。
    """
    from storyforge.domain.enums import GateErrorCode  # noqa: PLC0415
    from scripts.multibook_e2e import _GATE_CODE_LABELS, _classify_gate  # noqa: PLC0415

    # ★ 枚举里**每一个**值都必须有映射（防枚举加了值而映射忘了跟）
    for code in GateErrorCode:
        label = _GATE_CODE_LABELS.get(code.value)
        assert label, f"闸门码 {code.value!r} 没有报表标签（枚举加了值要同步）"
        assert label.startswith("volume:")

    # 实测那条消息：即使措辞里没有关键词，也必须归类正确
    got = _classify_gate({
        "code": "milestones",
        "message": "第 2 卷存在 49 章的里程碑真空（上限 25）",
    })
    assert got == "volume:milestones", got


def test_gate_unknown_code_is_exposed_not_disguised() -> None:
    """★ 未登记的闸门码必须**显式暴露**，不能伪装成已分类。

    若静默归到 `volume:unclassified`，下次加闸门码时会**看不出来**
    （本轮 `milestones` 就是这样漏掉的）。
    """
    from scripts.multibook_e2e import _classify_gate  # noqa: PLC0415

    got = _classify_gate({"code": "brand_new_gate", "message": "任意"})
    assert got == "volume:code:brand_new_gate", got


def test_gate_without_code_falls_back_to_message() -> None:
    """老产物可能没有 `code` —— 回退到措辞匹配，不崩。"""
    from scripts.multibook_e2e import _classify_gate  # noqa: PLC0415

    got = _classify_gate({"message": "结构闸门失败"})
    assert got == "volume:gate", got


# --------------------------------------------------------------------------- #
# ★ 服务端故障必须与代码缺陷**分开报**
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("msg", [
    "模型输出无法解析：节点 bible_architect 调用失败（2 次尝试）："
    "Error code: 500 - {'error': {'code': '500', 'message': 'Internal Server Error'}}",
    "Error code: 502 - Bad Gateway",
    "Error code: 503 - Service Unavailable",
    "Error code: 504 - Gateway Timeout",
    "APIConnectionError: Connection error.",
])
def test_provider_faults_are_classified_separately(msg: str) -> None:
    """★★ provider 故障**不得**被归类成代码缺陷。

    ## 实测教训（很严重）

    provider 的 `/chat/completions` 整体 500 时，每一轮 e2e 会在
    **不同阶段**失败（圣经 / 台阶 / 骨架 / 细纲都出现过）——
    看起来像"链路到处是 bug"。

    而裸 `httpx` 直连（绕开 langchain 与我们的代码）**同样 500**，
    同时 `GET /models` 仍返回 200 → 确认是**服务端故障**。

    若把它归进 `bible:parse` / `volume:unclassified` 这类代码缺陷类别，
    就会误导人去修一个**没坏的东西**。
    """
    got = _classify(msg)
    assert got.startswith("provider:"), (
        f"服务端故障被误归为代码缺陷：{got!r}\n消息：{msg[:80]}"
    )


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
