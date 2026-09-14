"""结构闸门测试（对策② / 痛点 5）。

★ 本文件的验收核心：用**旧项目的真实坏数据**验证闸门确实能拦住。
   若闸门拦不住书 23 那种「400 章全在第 1 卷」，它就是无用的。
"""

from __future__ import annotations

import pytest

from storyforge.domain.outline import (
    ChapterPlan,
    Milestone,
    SceneBeat,
    SeriesPlan,
    VolumePlan,
    WorldStateSnapshot,
)
from storyforge.domain.validators.structure_gate import (
    GateConfig,
    GateErrorCode,
    evaluate_series,
    evaluate_volume_reuse,
)

pytestmark = pytest.mark.unit


def mk_volume(
    no: int,
    start: int,
    end: int,
    *,
    locations: list[str] | None = None,
    factions: list[str] | None = None,
    antagonist: str = "",
    escalation: str = "",
    closure: str = "旧地图收尾",
    milestones: int = 0,
) -> VolumePlan:
    n = end - start + 1
    ms = [
        Milestone(chapter_no=start + i * max(1, n // max(1, milestones)), key=f"m{i}")
        for i in range(milestones)
    ]
    return VolumePlan(
        volume_no=no,
        title=f"第{no}卷",
        new_locations=locations if locations is not None else ["新地图A", "新地图B"],
        new_factions=factions if factions is not None else ["新势力A", "新势力B"],
        antagonist=antagonist or f"对手{no}",
        power_ceiling=f"上限{no}",
        core_conflict=f"矛盾{no}",
        escalation_from_prev=escalation or f"从{no-1}卷的村级冲突升级到{no}卷的城级对决",
        old_map_closure=closure,
        volume_end_hook=f"钩子{no}",
        chapter_start=start,
        chapter_end=end,
        milestones=ms,
        goal=f"目标{no}",
    )


def healthy_plan() -> SeriesPlan:
    """一个健康的多卷规划（每卷 200 章、有扩张、有里程碑）。"""
    vols = []
    for i, (start, end) in enumerate([(1, 200), (201, 400), (401, 600)], start=1):
        vols.append(
            mk_volume(
                i, start, end,
                locations=[f"{i}号新城", f"{i}号秘境"],
                factions=[f"{i}号宗门", f"{i}号世家"],
                antagonist=f"第{i}位大敌",
                milestones=10,
            )
        )
    return SeriesPlan(book_id=1, total_volumes=3, target_chapters=600, volumes=vols)


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #


def test_healthy_plan_passes() -> None:
    result = evaluate_series(healthy_plan())
    assert result.ok, f"健康规划不应被拦：{[i.message for i in result.issues]}"
    assert not result.blocking
    assert result.metrics["volumes"] == 3.0
    assert result.metrics["total_new_locations"] == 6.0


# --------------------------------------------------------------------------- #
# ★ 核心：拦住旧项目的真实坏数据
# --------------------------------------------------------------------------- #


def test_blocks_book23_pattern_400_chapters_in_volume_1() -> None:
    """★ 书 23 的真实形态：400 章全在第 1 卷，后续 4 卷只有占位目标。

    实测数据：
        chapters: list[400]  →  卷分配 {1: 400}
        volumes[1..4].blueprint = "missing"
        plot_nodes 仅 4 个（要求 ≥9）
        第 1 卷 vol_goal = 「在长明楼读透三十七条」

    旧项目的 `ultra_structure_config` **设计对了这些指标但 enabled=false**，
    所以这本书顺利写了 400 章。本测试确保闸门真的会拦。
    """
    v1 = VolumePlan(
        volume_no=1, title="值夜手册",
        new_locations=["长明楼"],
        new_factions=["档案馆"],
        antagonist="老周",
        escalation_from_prev="承接开篇",
        old_map_closure="",            # 后续卷没有收尾说明
        chapter_start=1, chapter_end=400,
        milestones=[],                 # 整个第 1 卷无里程碑
        goal="陈砚上手值夜，读透三十七条",
    )
    # 后续卷是占位（blueprint=missing 的等价形态：字段全空）
    placeholders = [
        VolumePlan(volume_no=i, title=f"占位卷{i}", chapter_start=401,
                   chapter_end=401, goal="")
        for i in (2, 3, 4, 5)
    ]
    plan = SeriesPlan(
        book_id=23, total_volumes=5, target_chapters=400,
        volumes=[v1] + placeholders,
    )

    result = evaluate_series(plan)

    assert not result.ok, "必须拦住书 23 的形态"
    assert result.blocking, "必须**阻断**（不能只是 shadow 告警）"
    codes = {i.code for i in result.issues}
    # 必须检出：章数失衡、新地图不足、新势力不足、里程碑缺失、旧地图未收尾
    assert GateErrorCode.CHAPTER_DISTRIBUTION in codes
    assert GateErrorCode.NEW_LOCATIONS in codes
    assert GateErrorCode.NEW_FACTIONS in codes
    assert GateErrorCode.MILESTONE_DENSITY in codes
    assert GateErrorCode.OLD_MAP_CLOSURE in codes
    assert result.metrics["max_volume_share"] == 1.0


def test_blocks_book24_template_escalation() -> None:
    """★ 书 24 的模板句：`vol_end_hook` = 「推向「首次记忆剥夺」的下一个阶段」。

    实测：`volumes[0..2].main_line = ""`，卷 2 的 `vol_end_hook` 是模板填充。
    """
    v = mk_volume(2, 201, 400, escalation="推向「首次记忆剥夺」的下一个阶段")
    plan = SeriesPlan(
        book_id=24, total_volumes=2, target_chapters=400,
        volumes=[mk_volume(1, 1, 200, milestones=8), v],
    )
    result = evaluate_series(plan)
    codes = {i.code for i in result.issues}
    assert GateErrorCode.ESCALATION_QUALITY in codes, "模板句必须被检出"
    assert not result.ok


@pytest.mark.parametrize(
    "template",
    [
        "推向「秘境探秘」的下一个阶段",
        "持续推进主线剧情",
        "开启新篇章",
        "故事继续发展",
        "进一步发展",
        "短",          # 过短（<8 字符）
    ],
)
def test_template_escalations_are_blocked(template: str) -> None:
    """模板句 / 过短内容必须被检出。

    ⚠️ 注意空字符串单独测（`mk_volume` 用 `or` 兜底，空串会走默认值）。
    """
    v = mk_volume(2, 201, 400)
    v.escalation_from_prev = template
    plan = SeriesPlan(
        book_id=1, total_volumes=2, target_chapters=400,
        volumes=[mk_volume(1, 1, 200, milestones=8), v],
    )
    result = evaluate_series(plan)
    assert GateErrorCode.ESCALATION_QUALITY in {i.code for i in result.issues}, (
        f"模板句 {template!r} 未被检出"
    )


def test_empty_escalation_is_blocked() -> None:
    """空升级说明必须被检出（直接构造对象，绕过辅助函数的兜底）。"""
    v = mk_volume(2, 201, 400)
    v.escalation_from_prev = ""
    assert v.is_template_escalation() is True
    plan = SeriesPlan(
        book_id=1, total_volumes=2, target_chapters=400,
        volumes=[mk_volume(1, 1, 200, milestones=8), v],
    )
    assert GateErrorCode.ESCALATION_QUALITY in {
        i.code for i in evaluate_series(plan).issues
    }


def test_blocks_milestone_vacuum_like_book23() -> None:
    """★ 书 23 实测：400 章只有 4 个里程碑，且 3 → 205 之间有 **202 章真空**。"""
    v = VolumePlan(
        volume_no=1, title="卷一",
        new_locations=["A", "B"], new_factions=["F1", "F2"],
        antagonist="敌", escalation_from_prev="从平静到危机四伏的升级",
        old_map_closure="收尾", chapter_start=1, chapter_end=400,
        milestones=[
            Milestone(chapter_no=3, key="m1"),
            Milestone(chapter_no=205, key="m2"),
            Milestone(chapter_no=318, key="m3"),
            Milestone(chapter_no=400, key="m4"),
        ],
    )
    plan = SeriesPlan(book_id=23, total_volumes=1, target_chapters=400, volumes=[v])
    result = evaluate_series(plan)

    ms_issues = [i for i in result.issues if i.code == GateErrorCode.MILESTONE_DENSITY]
    assert ms_issues, "必须检出里程碑不足与真空"
    assert any("真空" in i.message for i in ms_issues), (
        f"必须检出 202 章真空：{[i.message for i in ms_issues]}"
    )


def test_blocks_duplicate_antagonist() -> None:
    """对手阶梯：各卷主要对手必须不同。

    旧项目 `ultra_structure_config` 要求「层级随卷单调递增」，
    但实际书 23/24 的对手贯穿全书。
    """
    vols = [
        mk_volume(1, 1, 200, antagonist="同一个大反派", milestones=8),
        mk_volume(2, 201, 400, antagonist="同一个大反派", milestones=8),
    ]
    plan = SeriesPlan(book_id=1, total_volumes=2, target_chapters=400, volumes=vols)
    result = evaluate_series(plan)
    assert GateErrorCode.ANTAGONIST_VARIETY in {i.code for i in result.issues}


def test_blocks_duplicate_new_locations_across_volumes() -> None:
    """"新地图"不得与既往卷重复 —— 防模型换前缀凑数。

    旧项目 `ultra_structure_repair` 实测：模型补线时会**换前缀/加后缀凑数**，
    精确名去重拦不住（所以要用 `_arc_core_key` 剥离前缀）。
    """
    vols = [
        mk_volume(1, 1, 200, locations=["青云城", "落霞谷"], milestones=8),
        mk_volume(2, 201, 400, locations=["青云城", "新地图"], milestones=8),
    ]
    plan = SeriesPlan(book_id=1, total_volumes=2, target_chapters=400, volumes=vols)
    result = evaluate_series(plan)
    loc_issues = [i for i in result.issues if i.code == GateErrorCode.NEW_LOCATIONS]
    assert any("已出现过" in i.message for i in loc_issues), (
        f"重复地图必须被检出：{[i.message for i in loc_issues]}"
    )


def test_blocks_volume_count_mismatch() -> None:
    """声明 5 卷但只规划 1 卷 —— 正是旧项目 `blueprint="missing"` 的形态。"""
    plan = SeriesPlan(
        book_id=1, total_volumes=5, target_chapters=400,
        volumes=[mk_volume(1, 1, 400, milestones=16)],
    )
    result = evaluate_series(plan)
    assert GateErrorCode.VOLUME_COUNT in {i.code for i in result.issues}


def test_blocks_volume_size_out_of_range() -> None:
    """卷规模超出相对占比区间 → 阻断。

    ⚠️ 口径已从「绝对章数 [150,250]」改为「相对占比 + 绝对下限」，
    原因见 `test_relative_share_does_not_lock_volume_count`。
    这里第 1 卷占 400/600 = 67% > 60% → 仍应阻断。
    """
    plan = SeriesPlan(
        book_id=1, total_volumes=2, target_chapters=600,
        volumes=[mk_volume(1, 1, 400, milestones=16), mk_volume(2, 401, 600, milestones=8)],
    )
    result = evaluate_series(plan)
    assert GateErrorCode.CHAPTER_DISTRIBUTION in {i.code for i in result.issues}


def test_relative_share_does_not_lock_volume_count() -> None:
    """★ 相对占比**不得锁死卷数**（实测过约束修正）。

    绝对区间 `[150, 250]` 配合「各卷之和 = 目标章数」会锁死卷数：
        目标 400 章 → 只可能 2 卷（200+200）
        目标 800 章 → 只可能 4 卷（200×4）

    实测模型规划 **4 卷 × 100 章**（400 章，结构完全合理）时被拒，
    而它**无法知道**唯一合法解是"正好 2 卷" —— 闸门在替作者定卷数，
    而不是在检查质量。

    改相对占比后：4 × 25% 应当**通过**。
    """
    plan = SeriesPlan(
        book_id=1, total_volumes=4, target_chapters=400,
        volumes=[
            mk_volume(1, 1, 100, milestones=4),
            mk_volume(2, 101, 200, milestones=4),
            mk_volume(3, 201, 300, milestones=4),
            mk_volume(4, 301, 400, milestones=4),
        ],
    )
    result = evaluate_series(plan)
    dist = [i for i in result.issues if i.code == GateErrorCode.CHAPTER_DISTRIBUTION]
    assert not dist, f"4 卷各占 25% 应当通过，实测问题：{dist}"


def test_two_volume_split_still_passes() -> None:
    """回归：2 卷 × 200 章（400 章）在新口径下仍然通过。"""
    plan = SeriesPlan(
        book_id=1, total_volumes=2, target_chapters=400,
        volumes=[mk_volume(1, 1, 200, milestones=8),
                 mk_volume(2, 201, 400, milestones=8)],
    )
    result = evaluate_series(plan)
    dist = [i for i in result.issues if i.code == GateErrorCode.CHAPTER_DISTRIBUTION]
    assert not dist, dist


def test_fragmented_volumes_blocked_by_absolute_floor() -> None:
    """★ 相对区间之外仍要**绝对下限**：防"每卷 3 章"的碎片化。

    占比检查对碎片化是无效的（20 卷 × 5% 全部"合规"），
    所以下限必须独立存在。
    """
    plan = SeriesPlan(
        book_id=1, total_volumes=4, target_chapters=400,
        volumes=[
            mk_volume(1, 1, 10, milestones=1),
            mk_volume(2, 11, 20, milestones=1),
            mk_volume(3, 21, 30, milestones=1),
            mk_volume(4, 31, 400, milestones=16),
        ],
    )
    result = evaluate_series(plan)
    msgs = [i.message for i in result.issues
            if i.code == GateErrorCode.CHAPTER_DISTRIBUTION]
    assert any("绝对下限" in m for m in msgs), msgs


def test_single_volume_dominating_book_is_blocked() -> None:
    """★ 旧项目真实缺陷：400 章**全挤第 1 卷**（占 100%）必须被拦。

    这是与"锁死卷数"要区分开的那件事 —— 修掉过约束**不能**把真正的
    缺陷一起放走。
    """
    plan = SeriesPlan(
        book_id=1, total_volumes=5, target_chapters=400,
        volumes=[mk_volume(1, 1, 400, milestones=16)],
    )
    plan.total_volumes = 1  # 只规划了 1 卷
    result = evaluate_series(plan)
    assert not result.ok


# --------------------------------------------------------------------------- #
# 地点复用（防「全书困在一处」）
# --------------------------------------------------------------------------- #


def test_location_reuse_blocks_single_building_book() -> None:
    """★ 书 23 形态：400 章困在一栋楼里。

    实测场景 token 主体是「门/电梯/楼层/走廊」，唯一地标「长明楼」只出现 5 次。
    对应的结构化指标是「单地点章节占比过高」。
    """
    # 100 章中 90 章在同一个地点实体（id=1）
    locations = [1] * 90 + [2] * 8 + [3] * 2
    v = mk_volume(1, 1, 100, milestones=4)
    result = evaluate_volume_reuse(v, locations)

    assert not result.ok
    assert result.blocking
    assert GateErrorCode.LOCATION_REUSE in {i.code for i in result.issues}
    assert result.metrics["location_reuse_ratio"] == pytest.approx(0.9)


def test_location_reuse_passes_when_diverse() -> None:
    locations = [1] * 20 + [2] * 20 + [3] * 20 + [4] * 20 + [5] * 20
    v = mk_volume(1, 1, 100, milestones=4)
    result = evaluate_volume_reuse(v, locations)
    assert result.ok
    assert result.metrics["distinct_locations"] == 5.0


def test_location_reuse_handles_none_locations() -> None:
    """`None` 地点（未记录）不应被当成"地点 0"统计。"""
    locations: list[int | None] = [None] * 50 + [1] * 50
    v = mk_volume(1, 1, 100, milestones=4)
    result = evaluate_volume_reuse(v, locations)
    assert result.metrics["location_reuse_ratio"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# shadow 与 enabled 语义
# --------------------------------------------------------------------------- #


def test_shadow_mode_reports_but_does_not_block() -> None:
    """shadow 模式只报告不阻断。

    ⚠️ 但**默认必须是 shadow=False** —— 旧项目正是 shadow=true
    导致 400 章挤第 1 卷与 412 项 lint error 全部放行。
    """
    bad = SeriesPlan(
        book_id=1, total_volumes=2, target_chapters=400,
        volumes=[mk_volume(1, 1, 400, milestones=0)],
    )
    blocking_cfg = GateConfig(shadow=False)
    shadow_cfg = GateConfig(shadow=True)

    assert evaluate_series(bad, blocking_cfg).blocking is True
    shadow_result = evaluate_series(bad, shadow_cfg)
    assert shadow_result.ok is False
    assert shadow_result.blocking is False
    assert "[SHADOW]" in shadow_result.summary()


def test_default_shadow_is_false() -> None:
    """★ 默认不得是 shadow —— 这是旧项目最贵的教训之一。"""
    assert GateConfig().shadow is False
    assert GateConfig().enabled is True


def test_disabled_gate_passes_everything() -> None:
    bad = SeriesPlan(book_id=1, total_volumes=1, target_chapters=400, volumes=[])
    result = evaluate_series(bad, GateConfig(enabled=False))
    assert result.ok


# --------------------------------------------------------------------------- #
# 失败分类（供增量补缺修复）
# --------------------------------------------------------------------------- #


def test_issues_are_classified_for_incremental_repair() -> None:
    """失败必须按 error_code 分类 —— 旧项目实测**禁止全量重写**。

    `docs/AGENTS.md`：
        **修复必须走增量补缺，禁止回退成「全量重写整份故事 JSON」**
        —— 大输出必被模型截断（book23/25 两次 ROLLED_BACK 的根因）。
        补丁按 error_code 分类：arcs/beats/capacity/milestones/factions/
        characters/volumes，各自小 prompt。
    """
    bad = SeriesPlan(
        book_id=1, total_volumes=2, target_chapters=400,
        volumes=[
            VolumePlan(volume_no=1, title="", chapter_start=1, chapter_end=400),
            VolumePlan(volume_no=2, title="", chapter_start=401, chapter_end=401),
        ],
    )
    result = evaluate_series(bad)
    by_code = result.by_code()
    assert len(by_code) >= 3, f"应分成多类以便增量修复，实测 {list(by_code)}"
    for code, items in by_code.items():
        assert GateErrorCode(code)  # 都是合法枚举
        assert all(i.code.value == code for i in items)


def test_gate_result_summary_mentions_codes() -> None:
    bad = SeriesPlan(book_id=1, total_volumes=1, target_chapters=400,
                     volumes=[mk_volume(1, 1, 400, milestones=0)])
    s = evaluate_series(bad).summary()
    assert "BLOCKED" in s
    assert "milestones" in s


# --------------------------------------------------------------------------- #
# ChapterPlan lint（细纲确定性校验）
# --------------------------------------------------------------------------- #


def test_chapter_plan_lint_catches_missing_fields() -> None:
    """细纲 lint：继承旧项目「缺省即不通过」的口径。

    旧项目实测落地率：`plot_detail` p50 788（下限 900）、
    `entry_state` 13%、`milestone_ids` 0%。
    """
    cp = ChapterPlan(global_no=1, volume_no=1, title="", plot="", scenes=[])
    issues = cp.lint()
    assert any("标题" in i for i in issues)
    assert any("plot" in i for i in issues)
    assert any("场景数" in i for i in issues)
    assert any("entry_state" in i for i in issues)
    assert any("end_hook" in i for i in issues)


def test_chapter_plan_lint_catches_incomplete_scene() -> None:
    cp = ChapterPlan(
        global_no=1, volume_no=1, title="t", plot="p",
        scenes=[SceneBeat(seq=0, goal="有目标但缺其他")],
    )
    issues = cp.lint()
    assert any("不完整" in i for i in issues)


def test_chapter_plan_lint_passes_when_complete() -> None:
    scenes = [
        SceneBeat(seq=i, goal=f"g{i}", obstacle=f"o{i}", outcome=f"r{i}", why_now=f"w{i}")
        for i in range(4)
    ]
    cp = ChapterPlan(
        global_no=1, volume_no=1, title="标题", plot="梗概",
        scenes=scenes,
        entry_state=WorldStateSnapshot(location_entity_id=1, story_time="夜"),
        exit_state=WorldStateSnapshot(location_entity_id=1, story_time="深夜"),
        end_hook="钩子",
    )
    assert cp.lint() == []


def test_chapter_plan_lint_catches_health_beat_without_scene() -> None:
    """伤势节拍必须指向真实场景（否则仍是凭空痊愈）。"""
    from storyforge.domain.enums import HealthAction
    from storyforge.domain.health import HealthBeat

    scenes = [
        SceneBeat(seq=i, goal="g", obstacle="o", outcome="r", why_now="w")
        for i in range(2)
    ]
    cp = ChapterPlan(
        global_no=1, volume_no=1, title="t", plot="p", scenes=scenes,
        entry_state=WorldStateSnapshot(location_entity_id=1),
        exit_state=WorldStateSnapshot(location_entity_id=1),
        end_hook="h",
        health_beats=[
            HealthBeat(injury_id=1, action=HealthAction.HEALED, chapter_no=1, scene_seq=9)
        ],
    )
    issues = cp.lint()
    assert any("伤势节拍" in i for i in issues)


# --------------------------------------------------------------------------- #
# entry/exit 连续性（结构化校验）
# --------------------------------------------------------------------------- #


def test_structured_state_detects_location_jump() -> None:
    """结构化 entry/exit 让「地点跳变」可机械检出。

    旧项目 `outline_lint.py` 只能做字符串比对，无法定位"哪里不一致"。
    """
    a = WorldStateSnapshot(location_entity_id=1, story_time="夜", present_entity_ids=[1])
    b = WorldStateSnapshot(location_entity_id=2, story_time="夜", present_entity_ids=[1])
    problems = a.continuity_problems(b)
    assert problems and "地点跳变" in problems[0]


def test_structured_state_passes_when_consistent() -> None:
    a = WorldStateSnapshot(location_entity_id=1, story_time="夜")
    b = WorldStateSnapshot(location_entity_id=1, story_time="深夜")
    assert a.continuity_problems(b) == []


def test_world_state_snapshot_emptiness() -> None:
    assert WorldStateSnapshot().is_empty()
    assert not WorldStateSnapshot(location_entity_id=1).is_empty()
