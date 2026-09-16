"""章间连续性检查（`graph/nodes/continuity.py`）。

## 本文件防的是什么

连续性是**做对的工作被扔掉**的直接原因：细纲 LLM 调用成功、
内容真的生成了，却被整批丢弃（`ok=False plans=0`）。

实测三类假阳性（6 书回归的 21 对真实相邻对里，6 对报警、**4 对是假阳性**）：

    书15 铁门关驿丞署·院内 → 铁门关驿丞署·前堂    （同一驿站，不同房间）
    书16 市公安局地下…走廊 → 市局地下…走廊        （市公安局 = 市局，缩写）
    书13 仁华医院…病房     → 仁华医院…病房**外**   （只是门外）
    书12 秦牧的柴房       → 杂役坊柴房区          （共享「柴房」，边界情形）

前三条修好；第四条**刻意不修**（原因见下）。
"""

from __future__ import annotations

import pytest

from storyforge.domain.enums import BeatType
from storyforge.domain.outline import ChapterPlan, SceneBeat, WorldStateSnapshot
from storyforge.graph.nodes.continuity import continuity_issues

pytestmark = pytest.mark.unit


def plan(no: int, entry: str = "", exit_: str = "", *,
         is_transition: bool = False, beat: BeatType = BeatType.NORMAL,
         scenes: int = 4) -> ChapterPlan:
    return ChapterPlan(
        global_no=no, volume_no=1, title=f"t{no}", plot="剧情梗概" * 5,
        beat_type=beat, is_transition=is_transition,
        scenes=[SceneBeat(seq=i, goal="g", obstacle="o", outcome="r", why_now="w")
                for i in range(scenes)],
        entry_state=WorldStateSnapshot(location_name=entry),
        exit_state=WorldStateSnapshot(location_name=exit_),
        end_hook="钩子",
    )


# --------------------------------------------------------------------------- #
# ① 基本判据
# --------------------------------------------------------------------------- #


def test_same_location_passes() -> None:
    assert continuity_issues(plan(1, exit_="长明楼"), plan(2, entry="长明楼")) == []


def test_empty_location_passes() -> None:
    """地点缺失不是"跳变"（由别的 lint 管，不在这里重复报）。"""
    assert continuity_issues(plan(1, exit_=""), plan(2, entry="长明楼")) == []
    assert continuity_issues(plan(1, exit_="长明楼"), plan(2, entry="")) == []


def test_declared_transition_passes() -> None:
    """模型声明了换场 → 放过（`is_transition` 信号）。"""
    assert continuity_issues(
        plan(1, exit_="长明楼"), plan(2, entry="镇公所", is_transition=True)
    ) == []


def test_beat_type_transition_passes() -> None:
    """`beat_type == TRANSITION` 是**第二个独立信号**：
    模型可能只填了节拍而忘了同时置旗。"""
    assert continuity_issues(
        plan(1, exit_="长明楼"),
        plan(2, entry="镇公所", is_transition=False, beat=BeatType.TRANSITION),
    ) == []


def test_genuine_jump_is_reported() -> None:
    """真换场且未声明 → **必须**报警（证明没有放水）。"""
    issues = continuity_issues(plan(1, exit_="长明楼"), plan(2, entry="镇公所"))
    assert issues and "地点跳变" in issues[0]


# --------------------------------------------------------------------------- #
# ② ★ 三类实测假阳性（必须都消掉）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("a,b,why", [
    ("铁门关驿丞署·院内", "铁门关驿丞署·前堂", "同一驿站，不同房间"),
    ("市公安局地下特殊物证库入口走廊", "市局地下特殊物证库入口走廊",
     "『市公安局』= 『市局』，纯缩写"),
    ("仁华医院为王女士安排的临时安全病房",
     "仁华医院为王女士安排的临时安全病房外", "只是门外"),
])
def test_real_false_positives_are_fixed(a: str, b: str, why: str) -> None:
    """★ 6 书回归里实测的三类假阳性，必须不再报警。

    每一条都让**整批细纲作废**（做对的工作被扔掉）。
    """
    issues = continuity_issues(plan(1, exit_=a), plan(2, entry=b))
    assert issues == [], f"「{why}」仍被误报：{issues}"


# --------------------------------------------------------------------------- #
# ③ ★ 刻意不修的那条（记录理由，防止后人"顺手放宽阈值"）
# --------------------------------------------------------------------------- #


def test_boundary_case_is_deliberately_still_reported() -> None:
    """★ 边界情形**仍然报警** —— 这是**刻意**的，不是遗漏。

    ## 数据（`scripts/diag_pairs_verdict.py`）

        书12  秦牧的柴房    →  杂役坊柴房区        LCS = 2（共享「柴房」）
        书13  …医院住院部外  →  仁华医院法务部…     LCS = 2（共享「医院」）

    **两条的 LCS 完全相同，但性质相反**：
      · 前者是同一类建筑的不同叫法（严格说也不是同一地点）
      · 后者是**真换场**（住院部 vs 法务部，跨部门）

    ## 所以不能调阈值

    把 LCS 从 4 降到 2 → 修好前者，**必然弄坏后者**（放过真换场）。
    两者在**字符串层面不可区分** —— 这说明"是否同地点"本质上需要
    **语义/层级**信息（`LocationLevel` 目前只有定义、零实现），
    不是字符串阈值能解决的。

    现状：该情形由**模型声明换场**（`is_transition`）+ 审校层处理。
    D-16：**单条失败不得直接触发"放宽阈值"**。
    """
    issues = continuity_issues(
        plan(1, exit_="秦牧的柴房"), plan(2, entry="杂役坊柴房区")
    )
    assert issues, (
        "这条目前仍会报警（刻意保留）。若你把它改成通过，"
        "请先解释：为什么真换场那条（LCS 同样=2）不会被一起放过？"
    )


def test_lcs_two_is_not_enough_to_prove_same_place() -> None:
    """★ 量化依据：LCS=2 的两个例子**性质相反**，阈值无法区分它们。

    这是"不调阈值"的**可执行证据**（而不是"我觉得不能调"）。
    """
    from storyforge.graph.nodes.skeleton_planner import (  # noqa: PLC0415
        _lcs_len,
        _normalize_for_match,
        _mentions,
    )

    same_place = ("秦牧的柴房", "杂役坊柴房区")
    real_jump = ("上海某三甲医院住院部外", "仁华医院法务部办公室门口")

    lcs_same = _lcs_len(_normalize_for_match(same_place[1]),
                        _normalize_for_match(same_place[0]))
    lcs_jump = _lcs_len(_normalize_for_match(real_jump[1]),
                        _normalize_for_match(real_jump[0]))

    assert lcs_same == lcs_jump == 2, (
        f"前提变了：LCS 分别为 {lcs_same} / {lcs_jump}。"
        f"若两者不再相等，说明数据分布已变，可重新评估阈值。"
    )
    # 现行阈值（4）对两条都判"不同地点" —— 一条对、一条错，无法两全
    assert not _mentions(same_place[1], same_place[0])
    assert not _mentions(real_jump[1], real_jump[0])
