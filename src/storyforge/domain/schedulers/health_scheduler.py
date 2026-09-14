"""伤势节拍调度器（对策④核心，痛点 1）。

## 它解决的**确切**架构缺失

旧项目的能力矩阵（实测）：

    | 角色          | 能看到损伤 | 能规划治疗 | 能落实治疗 |
    | architect     | ❌         | ❌        | —         |
    | writer        | ✅         | ❌        | ⚠️ 只能照细纲写 |
    | post_processor| ✅         | ❌        | ❌        |
    | editor        | ✅         | ❌        | ⚠️ 只能打回重写 |

> **没有任何一个 agent 同时具备「看到损伤」和「规划本章节拍」两个能力。**

`architect.py` 中 `伤势|损伤` **0 命中** —— 细纲层**根本不规划伤**。
唯一的催促（`⚠️已超恢复期，本轮必须推进`）只到达 writer，
而 writer 必须服从细纲的"最高优先级、禁止新增细纲之外的重要转折"
→ 实测 writer **服从细纲**（这是正确的服从）→ 恢复永不发生（**恢复率实测 0%**）。

## 本调度器的职责

在**细纲规划时**（不是写作时）把恢复节拍注入 `ChapterPlan.health_beats`，
使恢复成为**细纲的一部分**。这样：
  · 细纲层"看到"了损伤（补上能力矩阵的缺口）
  · writer 拿到的是**有场景支撑的细纲**，不再面对矛盾指令
  · `heal_scene_planned=True` 之后才允许催促（消除死锁）

## 零 LLM

本模块是**纯确定性**的（`tests/contract` 强制 `scorecard/` 与 `domain/` 不依赖 LLM）。
可复现、零成本、可回归 —— 与"用 prompt 催促"形成本质区别。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storyforge.domain.enums import HealthAction, InjuryKind, InjuryStatus
from storyforge.domain.health import (
    NEVER_URGE,
    HealthBeat,
    HealPlan,
    Injury,
    ThresholdContext,
    effective_threshold,
    plan_heal,
)

#: 一次调度最多为多少章生成节拍（防一次性规划整本书）
DEFAULT_LOOKAHEAD = 30


@dataclass
class ScheduledChapterHealth:
    """某一章需要承载的伤势节拍。"""

    chapter_no: int
    beats: list[HealthBeat] = field(default_factory=list)
    #: 是否有"痊愈"节拍（供 `BeatType.HEAL` 判定）
    has_heal: bool = False

    @property
    def is_recovery_chapter(self) -> bool:
        return self.has_heal

    @property
    def beat_type_hint(self) -> str | None:
        """给细纲规划器的节拍类型提示。"""
        if self.has_heal:
            return "heal"
        if self.beats:
            return "recovery_prep"
        return None


@dataclass
class HealthSchedule:
    """一段章号区间内的伤势节拍安排。"""

    chapters: dict[int, ScheduledChapterHealth] = field(default_factory=dict)
    plans: dict[int, HealPlan] = field(default_factory=dict)
    #: 需要顺延恢复章的伤势（跑道不足）
    deferred: list[int] = field(default_factory=list)

    def for_chapter(self, chapter_no: int) -> ScheduledChapterHealth:
        return self.chapters.setdefault(chapter_no, ScheduledChapterHealth(chapter_no))

    @property
    def recovery_chapters(self) -> list[int]:
        return sorted(c for c, h in self.chapters.items() if h.has_heal)


class HealthScheduler:
    """确定性伤势节拍调度器。

    用法（细纲规划时调用）：

        sched = HealthScheduler(context_for_volume)
        schedule = sched.schedule(injuries, from_chapter=51, to_chapter=80)
        for ch_no, health in schedule.chapters.items():
            plan = chapter_plans[ch_no]
            plan.health_beats = health.beats      # ← 注入细纲
            plan.beat_type = health.beat_type_hint or plan.beat_type

    ## 设计要点

    1. **只规划未来窗口内**的节拍（默认 30 章），不一次性铺满全书 ——
       因为后续剧情会改变（角色可能提前痊愈或伤情恶化）。
    2. **已排入的节拍不重复**：调用方通过 `already_planned` 传入
       已存在于细纲中的 `(injury_id, chapter)` 对，避免重复注入。
    3. **不修改传入的 Injury 对象**（LangGraph 不可变约定）。
    """

    def __init__(
        self,
        *,
        lookahead: int = DEFAULT_LOOKAHEAD,
        prep_window: int = 4,
        volume_total_chapters: int | None = None,
    ) -> None:
        self.lookahead = lookahead
        self.prep_window = prep_window
        self.volume_total_chapters = volume_total_chapters

    def _ctx(self, injury: Injury, local_index: int | None) -> ThresholdContext:
        return ThresholdContext(
            # 主角/核心角色给更长窗口；这里由调用方在 Injury 上无此信息，
            # 故由 `core_entity_ids` 决定（见 schedule 参数）
            volume_total_chapters=self.volume_total_chapters,
            volume_local_index=local_index,
        )

    @staticmethod
    def _planned_chapters_for(
        injury_id: int, already_planned: set[tuple[int, int]]
    ) -> set[int]:
        return {ch for iid, ch in already_planned if iid == injury_id}

    def schedule(
        self,
        injuries: list[Injury],
        *,
        from_chapter: int,
        to_chapter: int | None = None,
        core_entity_ids: set[int] | None = None,
        equipment_by_entity: dict[int, tuple[str, ...]] | None = None,
        already_planned: set[tuple[int, int]] | None = None,
    ) -> HealthSchedule:
        """为 `[from_chapter, to_chapter]` 区间规划伤势节拍。

        参数：
          · `core_entity_ids` —— 核心实体集合（阈值 ×1.2）
          · `equipment_by_entity` —— 各实体当前装备（判定治疗资源 ×0.7/×1.3）
            ⚠️ 旧项目缺陷：`limited=True` 路径**不传 equip**
            → 配角永远按"无治疗资源"算，阈值被推后 ×1.3。
            这里由调度器**显式传入**，不存在"忘了传"的路径。
          · `already_planned` —— 已存在的 `(injury_id, chapter_no)` 对。

        ## 幂等性（★ 关键）

        一条伤势**只要已有任何节拍被排入细纲**，本次调度就**整条跳过**它，
        而不是逐条比对 `(injury_id, chapter)`。

        为什么：`plan_heal` 以 `from_chapter - 1` 为"当前章"推算节拍章号，
        两次调度的 `from_chapter` 不同会得到不同的章号分布
        → 逐条比对**必然漏网**（实测：重复调度注入 6 条重复节拍）。

        整条跳过是正确语义：伤势的恢复计划一旦开始执行，
        就不应该在后续批次的调度里被重新规划（那会导致同一伤势出现两套恢复节奏）。
        若剧情需要变更（提前痊愈/恶化），应显式走 `replan_injury()`。
        """
        end = to_chapter if to_chapter is not None else from_chapter + self.lookahead
        if end < from_chapter:
            raise ValueError(f"to_chapter({end}) 不得早于 from_chapter({from_chapter})")
        core = core_entity_ids or set()
        equip_map = equipment_by_entity or {}
        existing = already_planned or set()

        out = HealthSchedule()
        for injury in injuries:
            # 只处理活跃损伤；已痊愈/解除的跳过
            if injury.status in (InjuryStatus.HEALED, InjuryStatus.REMOVED):
                continue
            # 永久伤残：不规划痊愈
            if injury.is_permanent:
                continue
            # 诅咒：由伏笔驱动，不按章规划（旧项目声称如此但从未实现关联）
            #
            # ⚠️ 这里用**枚举成员**比较，不用 `.value == "诅咒"` 字面量：
            #    字面量比较在重命名枚举值时**静默失效**（比较永假，不报错），
            #    而后果正是旧项目那个「声称由伏笔驱动、实际无人关联」的形态 ——
            #    诅咒会掉进按章催促的路径。枚举成员比较则会被类型检查与
            #    重命名重构显式带出来。
            if injury.kind is InjuryKind.CURSE and injury.bound_foreshadow_key is None:
                continue
            # ★ 幂等：该伤势已有节拍排入细纲 → 整条跳过
            if self._planned_chapters_for(injury.id, existing):
                continue

            local_idx = None
            if self.volume_total_chapters:
                local_idx = max(1, from_chapter % self.volume_total_chapters or 1)

            ctx = ThresholdContext(
                is_core=injury.entity_id in core,
                equipment=tuple(equip_map.get(injury.entity_id, ())),
                volume_total_chapters=self.volume_total_chapters,
                volume_local_index=local_idx,
            )
            # 阈值仍为 0/999 的极端情况：跳过章数驱动的规划
            if effective_threshold(injury.kind, injury.severity, ctx) >= NEVER_URGE:
                continue

            plan = plan_heal(
                injury,
                current_chapter=from_chapter - 1,
                ctx=ctx,
                prep_window=self.prep_window,
            )
            if not plan.beats:
                continue

            # 只看落在窗口内的节拍
            in_window = [b for b in plan.beats if from_chapter <= b.chapter_no <= end]
            if not in_window:
                if plan.heal_chapter > end:
                    out.deferred.append(injury.id)
                continue

            out.plans[injury.id] = plan
            for beat in in_window:
                slot = out.for_chapter(beat.chapter_no)
                slot.beats.append(beat)
                if beat.action == HealthAction.HEALED:
                    slot.has_heal = True
        return out

    # -- 校验 ---------------------------------------------------------------

    @staticmethod
    def validate_beats_against_scenes(
        beats: list[HealthBeat], scene_count: int
    ) -> list[str]:
        """校验节拍能否落到真实场景上。

        细纲规划器必须在生成 `SceneBeat` 之后调用本方法，
        把 `scene_seq` 修正为真实存在的序号。返回问题列表（空 = 通过）。

        旧项目对照：细纲 schema 只有
        `plot/end_hook/plot_detail/entry_state/exit_state` 五个字段
        （`architect.py:1795-1812`），`entry_state` 的四要素是
        「地点·时间·在场人物·谁知道什么」—— **没有伤势**。
        本设计要求伤势节拍必须占用真实场景。
        """
        problems: list[str] = []
        for b in beats:
            if scene_count <= 0:
                problems.append(
                    f"伤势节拍（{b.action.value}，第{b.chapter_no}章）所在章没有任何场景"
                    f"—— 恢复必须由场景承载（否则仍是凭空痊愈）"
                )
            elif not (0 <= b.scene_seq < scene_count):
                problems.append(
                    f"伤势节拍（{b.action.value}，第{b.chapter_no}章）指向场景 "
                    f"{b.scene_seq}，但本章只有 {scene_count} 个场景"
                )
        return problems


def load_active_injuries(rows: list[dict]) -> list[Injury]:
    """从存储行构造领域对象（仓储层与本模块的唯一交界）。

    保持解析逻辑集中，避免"每个调用方各解析一遍"导致口径漂移
    （旧项目 `since_ch` 的卷内号/全书号混用正是口径漂移的后果）。
    """
    from storyforge.domain.enums import InjuryKind, InjurySeverity

    out: list[Injury] = []
    for r in rows:
        out.append(
            Injury(
                id=int(r["id"]),
                book_id=int(r["book_id"]),
                entity_id=int(r["entity_id"]),
                kind=InjuryKind(r["kind"]),
                name=str(r["name"]),
                severity=InjurySeverity(r["severity"]),
                status=InjuryStatus(r["status"]),
                since_chapter=int(r["since_chapter"]),
                updated_chapter=int(r["updated_chapter"]),
                expected_heal_chapter=(
                    int(r["expected_heal_chapter"])
                    if r.get("expected_heal_chapter") is not None
                    else None
                ),
                heal_scene_planned=bool(r.get("heal_scene_planned", False)),
                heal_condition=r.get("heal_condition"),
                is_permanent=bool(r.get("is_permanent", False)),
                resolved_chapter=(
                    int(r["resolved_chapter"])
                    if r.get("resolved_chapter") is not None
                    else None
                ),
            )
        )
    return out


__all__ = [
    "HealthScheduler",
    "HealthSchedule",
    "ScheduledChapterHealth",
    "load_active_injuries",
    "DEFAULT_LOOKAHEAD",
]
