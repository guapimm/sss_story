"""伤势模型与恢复调度测试（对策④ / 痛点 1）。

这些测试对应旧项目**实测恢复率 0%** 的根因，每条都防一个具体缺陷。
"""

from __future__ import annotations

import pytest

from storyforge.domain.enums import (
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
    HealthAction,
)
from storyforge.domain.health import (
    NEVER_URGE,
    HealthBeat,
    Injury,
    ThresholdContext,
    apply_status_change,
    base_threshold,
    effective_threshold,
    has_heal_resource,
    plan_heal,
    validate_recovery_claim,
)
from storyforge.domain.schedulers.health_scheduler import HealthScheduler

pytestmark = pytest.mark.unit


def mk_injury(
    *,
    iid: int = 1,
    kind: InjuryKind = InjuryKind.WOUND,
    severity: InjurySeverity = InjurySeverity.SEVERE,
    status: InjuryStatus = InjuryStatus.NEW,
    since: int = 10,
    expected: int | None = None,
    planned: bool = False,
    permanent: bool = False,
    entity_id: int = 1,
) -> Injury:
    return Injury(
        id=iid,
        book_id=1,
        entity_id=entity_id,
        kind=kind,
        name=f"伤{iid}",
        severity=severity,
        status=status,
        since_chapter=since,
        updated_chapter=since,
        expected_heal_chapter=expected,
        heal_scene_planned=planned,
        is_permanent=permanent,
    )


# --------------------------------------------------------------------------- #
# 阈值表穷尽性 —— 防「最重的伤反而永不催」（旧项目实测的注意力反向引导）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", list(InjuryKind))
@pytest.mark.parametrize("severity", list(InjurySeverity))
def test_every_kind_severity_has_threshold(kind: InjuryKind, severity: InjurySeverity) -> None:
    """`InjuryKind × InjurySeverity` **全组合**必须有阈值。

    旧项目 `memory.py:291-294` 自述：原表只登记「轻/重」两档，
    而模型实际会写「中」「极重」「轻微」→ `_impact_threshold` 拿不到 base
    → 返回 `None` → **这些损伤永远不会被催「本轮必须推进」**。
    实测书 24：最重的「神魂受创（伤势·极重）」**恰恰是唯一不催的那条**，
    而轻伤催得最凶 —— **注意力被反向引导**。
    """
    th = base_threshold(kind, severity)
    assert isinstance(th, int)
    assert th > 0


def test_severe_injury_is_still_urged() -> None:
    """最重的伤势必须**仍有阈值**（只是窗口更长），不能是 NEVER_URGE。

    这是对旧项目缺陷的直接回归测试：严重 ≠ 永不催促。
    """
    for kind in (InjuryKind.WOUND, InjuryKind.SEQUELA, InjuryKind.INNER_DEMON):
        assert base_threshold(kind, InjurySeverity.CRITICAL) < NEVER_URGE


def test_threshold_ordering_is_monotonic() -> None:
    """同一类别内，严重度越高阈值越大（窗口越长）。

    若不单调，说明表填错了 —— 轻伤比重伤更晚催是不合理的。
    """
    for kind in (InjuryKind.WOUND, InjuryKind.SEQUELA, InjuryKind.INNER_DEMON):
        vals = [base_threshold(kind, s) for s in InjurySeverity]
        assert vals == sorted(vals), f"{kind.value} 阈值非单调递增：{vals}"


def test_curse_is_not_chapter_urged() -> None:
    """诅咒不按章催促（由解咒伏笔驱动）。

    ⚠️ 但旧项目的缺陷是：注释说"由解咒伏笔驱动"，
    **却没有任何代码把解咒伏笔与诅咒关联起来**（`doc/research/02` §5.4）。
    本设计用 `bound_foreshadow_key` 建立关联（见调度器测试）。
    """
    for sev in InjurySeverity:
        assert base_threshold(InjuryKind.CURSE, sev) == NEVER_URGE


# --------------------------------------------------------------------------- #
# 修正因子
# --------------------------------------------------------------------------- #


def test_core_character_gets_longer_window() -> None:
    base = effective_threshold(InjuryKind.WOUND, InjurySeverity.SEVERE)
    core = effective_threshold(
        InjuryKind.WOUND, InjurySeverity.SEVERE, ThresholdContext(is_core=True)
    )
    assert core >= base


def test_heal_resource_shortens_window() -> None:
    """有治疗资源 → 窗口更短（恢复更快）。

    ⚠️ 旧项目缺陷：`_fmt_impacts` 读 `ctx["equip"]` 算这个因子，
    但 `_ctx_dmg` 初始化时**没有 `equip` 键**，`limited=True` 路径也没有
    → **配角永远按「无治疗资源」算，阈值 ×1.3 被推后**。
    本设计由调度器显式传 `equipment_by_entity`，不做隐式默认。
    """
    assert has_heal_resource(("金疮药",))
    assert not has_heal_resource(("青锋剑",))

    no_res = effective_threshold(
        InjuryKind.WOUND, InjurySeverity.SEVERE, ThresholdContext(equipment=("青锋剑",))
    )
    with_res = effective_threshold(
        InjuryKind.WOUND, InjurySeverity.SEVERE, ThresholdContext(equipment=("金疮药",))
    )
    assert with_res <= no_res


def test_volume_end_tightens_window() -> None:
    normal = effective_threshold(
        InjuryKind.WOUND, InjurySeverity.SEVERE,
        ThresholdContext(volume_total_chapters=100, volume_local_index=10),
    )
    vol_end = effective_threshold(
        InjuryKind.WOUND, InjurySeverity.SEVERE,
        ThresholdContext(volume_total_chapters=100, volume_local_index=95),
    )
    assert vol_end <= normal


def test_factor_is_clamped() -> None:
    """修正因子必须被 clamp 到 [0.6, 1.4]，不能无限放大/缩小。"""
    base = base_threshold(InjuryKind.WOUND, InjurySeverity.SEVERE)
    extreme = effective_threshold(
        InjuryKind.WOUND,
        InjurySeverity.SEVERE,
        ThresholdContext(
            is_core=True, equipment=(), volume_total_chapters=10, volume_local_index=10
        ),
    )
    assert 1 <= extreme <= int(base * 1.4) + 1


# --------------------------------------------------------------------------- #
# 「未排入细纲不得催促」—— 消除旧项目的死锁
# --------------------------------------------------------------------------- #


def test_no_urgency_without_planned_scene() -> None:
    """★ `heal_scene_planned=False` 时**必须不催促**。

    这是消除旧项目死锁的关键。旧项目的链条：

        architect 不排治疗 → 损伤区注入「本轮必须推进」
        → writer 服从细纲（最高优先级）→ editor 判「无治疗过程必改」
        → 修改稿仍须服从细纲 → **死循环，每章扣分，恢复永不发生**

    实测证据（`docs/TODO_20260913.md` §7.1）：
        1-9  5/10「多项标注『本轮必须推进』的损伤未推进」
    """
    inj = mk_injury(expected=15, planned=False)
    for ch in (10, 14, 15, 16, 50):
        assert inj.urgency(ch) is None, (
            f"第{ch}章：未排入细纲的伤势不得催促（旧项目死锁的根源）"
        )


def test_urgency_levels_after_planned() -> None:
    inj = mk_injury(expected=20, planned=True)
    assert inj.urgency(10) is None
    assert inj.urgency(18) == "approaching"
    assert inj.urgency(20) == "overdue"
    assert inj.urgency(30) == "overdue"


def test_permanent_injury_never_urged() -> None:
    """永久伤残不催促 —— 它出现在**永久失去名单**里，不需要"推进恢复"。"""
    inj = mk_injury(planned=True, permanent=True, expected=20)
    assert inj.urgency(50) is None


def test_healed_injury_not_urged() -> None:
    inj = apply_status_change(
        mk_injury(planned=True, expected=20), InjuryStatus.HEALED, chapter_no=20
    )
    assert not inj.is_active
    assert inj.urgency(30) is None
    assert inj.resolved_chapter == 20


# --------------------------------------------------------------------------- #
# 恢复计划：预热序列（解决「恢复太突然」）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("severity", list(InjurySeverity))
def test_heal_plan_beats_are_on_distinct_chapters(severity: InjurySeverity) -> None:
    """★ 预热节拍必须**每拍独占一章**。

    这是「恢复不能太突然」的量化保证。若 5 拍挤在 2 章内
    （如 ch11 求药 / ch11 恶化 / ch12 疗伤 / ch12 好转 / ch13 痊愈），
    那仍然是突变，只是把一句话换成了五句话。
    """
    plan = plan_heal(mk_injury(severity=severity), current_chapter=10)
    chapters = [b.chapter_no for b in plan.beats]
    assert len(chapters) == len(set(chapters)), f"有重复章号：{chapters}"
    assert chapters == sorted(chapters), f"章号未递增：{chapters}"


@pytest.mark.parametrize("severity", list(InjurySeverity))
def test_heal_plan_ends_with_healed(severity: InjurySeverity) -> None:
    plan = plan_heal(mk_injury(severity=severity), current_chapter=10)
    assert plan.beats[-1].action == HealthAction.HEALED
    assert plan.beats[-1].chapter_no == plan.heal_chapter
    assert all(b.chapter_no < plan.heal_chapter for b in plan.beats[:-1])


def test_worse_injury_has_longer_prep() -> None:
    """越重的伤，预热越长（更多准备环节）—— 符合叙事直觉。"""
    minor = plan_heal(mk_injury(severity=InjurySeverity.MINOR), current_chapter=10)
    critical = plan_heal(mk_injury(severity=InjurySeverity.CRITICAL), current_chapter=10)
    assert len(critical.beats) > len(minor.beats)


def test_insufficient_runway_defers_instead_of_compressing() -> None:
    """跑道不足时**顺延恢复章**，而不是把节拍压进同一章。

    这是设计决策：宁可晚几章恢复，也不要"突然痊愈"。
    """
    inj = mk_injury(severity=InjurySeverity.CRITICAL, expected=12)
    plan = plan_heal(inj, current_chapter=10)
    chapters = [b.chapter_no for b in plan.beats]
    assert len(chapters) == len(set(chapters))
    assert plan.heal_chapter >= 10 + 1 + 5, (
        f"跑道不足时必须顺延（CRITICAL 需 5 拍预热 + 1 痊愈），实测 heal_ch={plan.heal_chapter}"
    )


def test_expired_injury_plans_forward_not_backward() -> None:
    """已过期很久的伤势：恢复章必须在**当前章之后**，不追溯过去。"""
    inj = mk_injury(severity=InjurySeverity.CRITICAL, since=1)
    plan = plan_heal(inj, current_chapter=200)
    assert plan.heal_chapter > 200
    assert all(b.chapter_no >= 201 for b in plan.beats)


def test_curse_and_permanent_have_empty_plan() -> None:
    curse = mk_injury(kind=InjuryKind.CURSE, severity=InjurySeverity.SEVERE)
    perm = mk_injury(permanent=True)
    assert plan_heal(curse, current_chapter=10).beats == []
    assert plan_heal(perm, current_chapter=10).beats == []


# --------------------------------------------------------------------------- #
# 恢复校验：结构化证据（替代旧项目的自由文本关键词自锁）
# --------------------------------------------------------------------------- #


def test_recovery_requires_structured_beat() -> None:
    """报「痊愈」必须有对应章的 `HealthBeat` 支撑。

    旧项目靠**同一次 LLM 输出的 events** 里同时含角色名 + 25 个治疗语义词之一，
    而 prompt 只让写 2~5 条关键事件 → **过渡章必然自锁**。
    本设计改为查结构化节拍：确定性、无自锁、且仍能防住旧 bug
    （无关事件不会被误当痊愈依据）。
    """
    inj = mk_injury()
    beats = [HealthBeat(injury_id=1, action=HealthAction.HEALED, chapter_no=20, scene_seq=1)]

    ok = validate_recovery_claim(
        inj, target_status=InjuryStatus.HEALED, chapter_no=20, beats=beats, scene_count=4
    )
    assert ok.ok

    # 章号不匹配 → 拒绝
    bad_ch = validate_recovery_claim(
        inj, target_status=InjuryStatus.HEALED, chapter_no=99, beats=beats, scene_count=4
    )
    assert not bad_ch.ok

    # 无任何节拍 → 拒绝
    no_beat = validate_recovery_claim(
        inj, target_status=InjuryStatus.HEALED, chapter_no=20, beats=[], scene_count=4
    )
    assert not no_beat.ok


def test_recovery_beat_must_point_to_real_scene() -> None:
    """节拍必须落在真实场景上 —— 否则仍是"凭空痊愈"。"""
    inj = mk_injury()
    beats = [HealthBeat(injury_id=1, action=HealthAction.HEALED, chapter_no=20, scene_seq=9)]
    r = validate_recovery_claim(
        inj, target_status=InjuryStatus.HEALED, chapter_no=20, beats=beats, scene_count=3
    )
    assert not r.ok
    assert "场景" in r.reason


def test_healed_claim_requires_healed_action() -> None:
    """申报「痊愈」但节拍动作只是「好转」→ 拒绝（状态与节拍必须一致）。"""
    inj = mk_injury()
    beats = [
        HealthBeat(injury_id=1, action=HealthAction.RECOVERING, chapter_no=20, scene_seq=1)
    ]
    r = validate_recovery_claim(
        inj, target_status=InjuryStatus.HEALED, chapter_no=20, beats=beats, scene_count=4
    )
    assert not r.ok


def test_non_recovery_status_skips_check() -> None:
    """非恢复类状态（如「恶化」）不需要节拍支撑。"""
    inj = mk_injury()
    r = validate_recovery_claim(
        inj, target_status=InjuryStatus.WORSENED, chapter_no=20, beats=[], scene_count=4
    )
    assert r.ok


# --------------------------------------------------------------------------- #
# 不可变性（LangGraph 约定：节点不得原地改 state）
# --------------------------------------------------------------------------- #


def test_status_change_returns_new_object() -> None:
    original = mk_injury(status=InjuryStatus.NEW)
    updated = apply_status_change(original, InjuryStatus.HEALED, chapter_no=20)

    assert original.status == InjuryStatus.NEW, "原对象不得被修改"
    assert original.resolved_chapter is None
    assert updated.status == InjuryStatus.HEALED
    assert updated.resolved_chapter == 20


def test_permanent_status_sets_flag() -> None:
    original = mk_injury()
    updated = apply_status_change(
        original, InjuryStatus.PERMANENT_MITIGABLE, chapter_no=20
    )
    assert updated.is_permanent is True
    assert original.is_permanent is False


# --------------------------------------------------------------------------- #
# 调度器
# --------------------------------------------------------------------------- #


def test_scheduler_assigns_beats_across_chapters() -> None:
    sched = HealthScheduler(lookahead=40, volume_total_chapters=200)
    injuries = [
        mk_injury(iid=1, severity=InjurySeverity.SEVERE, since=10),
        mk_injury(iid=2, severity=InjurySeverity.LIGHT, since=11),
    ]
    schedule = sched.schedule(injuries, from_chapter=13, to_chapter=45)

    assert schedule.recovery_chapters, "必须规划出痊愈章"
    # 每章内同一伤势不得出现两次
    for ch, h in schedule.chapters.items():
        ids = [b.injury_id for b in h.beats]
        assert len(ids) == len(set(ids)), f"第{ch}章同一伤势出现多次"


def test_scheduler_does_not_mutate_inputs() -> None:
    sched = HealthScheduler()
    injuries = [mk_injury(iid=1)]
    before = (injuries[0].status, injuries[0].heal_scene_planned)
    sched.schedule(injuries, from_chapter=13, to_chapter=45)
    assert (injuries[0].status, injuries[0].heal_scene_planned) == before


def test_scheduler_is_idempotent() -> None:
    """★ 幂等：已有节拍的伤势**整条跳过**，不重复注入。

    实测教训：逐条比对 `(injury_id, chapter)` 会漏网 —— 因为
    `plan_heal` 以 `from_chapter - 1` 为基准推算章号，
    两次调度的 `from_chapter` 不同会得到不同分布（首次实现即踩此坑，注入 6 条重复）。
    """
    sched = HealthScheduler(lookahead=40)
    injuries = [mk_injury(iid=1), mk_injury(iid=2, severity=InjurySeverity.LIGHT)]

    first = sched.schedule(injuries, from_chapter=13, to_chapter=45)
    existing = {
        (b.injury_id, b.chapter_no) for h in first.chapters.values() for b in h.beats
    }
    assert existing

    second = sched.schedule(
        injuries, from_chapter=13, to_chapter=45, already_planned=existing
    )
    assert sum(len(h.beats) for h in second.chapters.values()) == 0

    # 不同窗口也不应重复规划已开始的伤势
    third = sched.schedule(
        injuries, from_chapter=20, to_chapter=60, already_planned=existing
    )
    assert sum(len(h.beats) for h in third.chapters.values()) == 0


def test_scheduler_skips_curse_without_foreshadow() -> None:
    """未绑伏笔的诅咒不按章规划 —— 旧项目声称"由伏笔驱动"但从未实现关联。"""
    sched = HealthScheduler()
    curse = mk_injury(iid=9, kind=InjuryKind.CURSE)
    schedule = sched.schedule([curse], from_chapter=13, to_chapter=45)
    assert 9 not in schedule.plans


def test_scheduler_skips_permanent() -> None:
    sched = HealthScheduler()
    perm = mk_injury(iid=8, permanent=True)
    schedule = sched.schedule([perm], from_chapter=13, to_chapter=45)
    assert 8 not in schedule.plans


def test_scheduler_expresses_heal_resource_influence() -> None:
    """治疗资源必须影响恢复速度（旧项目配角永远按无资源算）。"""
    sched = HealthScheduler()
    inj = mk_injury(iid=1, severity=InjurySeverity.SEVERE, since=10)

    without = sched.schedule(
        [inj], from_chapter=11, to_chapter=40, equipment_by_entity={1: ()}
    )
    with_res = sched.schedule(
        [inj], from_chapter=11, to_chapter=40, equipment_by_entity={1: ("金疮药",)}
    )
    assert with_res.plans[1].heal_chapter <= without.plans[1].heal_chapter


def test_validate_beats_against_scenes_reports_missing_scenes() -> None:
    beats = [HealthBeat(injury_id=1, action=HealthAction.HEALED, chapter_no=20, scene_seq=0)]
    problems = HealthScheduler.validate_beats_against_scenes(beats, scene_count=0)
    assert problems, "无场景时必须报错（恢复不能凭空发生）"

    problems_ok = HealthScheduler.validate_beats_against_scenes(beats, scene_count=3)
    assert not problems_ok
