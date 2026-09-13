"""伤势领域模型 + 恢复阈值表（对策④，痛点 1）。

## 旧项目的完整根因链（恢复率实测 **0%**）

1. **恢复靠每章 prompt 催促，不靠规划** —— 唯一的机制是在 states 注入里加一句
   `⚠️已超恢复期，本轮必须推进`（`memory.py:1001-1006`）。
2. **能力矩阵缺口**：没有任何 agent 同时具备「看到损伤」与「规划本章节拍」：

       | 角色          | 能看到损伤 | 能规划治疗 | 能落实治疗 |
       | architect     | ❌         | ❌        | —         |
       | writer        | ✅         | ❌        | ⚠️ 只能照细纲写 |
       | post_processor| ✅         | ❌        | ❌        |
       | editor        | ✅         | ❌        | ⚠️ 只能打回重写 |

   `architect.py` 中 `伤势|损伤` **0 命中** —— 细纲层**根本不规划伤**。
3. **writer 面对两条矛盾硬指令**：损伤区说「本轮必须推进」，
   细纲区说「最高优先级、禁止新增细纲之外的重要转折」
   → 实测 writer **服从细纲**（这是正确的服从）。
4. **抽取器自锁**：报「痊愈」要求**同一次 LLM 输出的 events** 里
   同时含角色名 + 治疗语义词，而 prompt 只让写 2~5 条关键事件
   → **过渡章必然自锁**。
5. **`VALID_BEATS` 无 `heal`** → 恢复不是合法节拍类型。

## 本模块的三条硬约束

1. `expected_heal_chapter` 由**调度器规划**出来（不是每章催促）
2. `heal_scene_planned=True` 才允许催 —— **消除旧项目死锁**
3. 痊愈需**结构化 health_beats** 支撑（不再靠自由文本关键词匹配）

## 阈值表：单一来源 + 穷尽性保证

⚠️ 旧项目最严重的枚举冲突：阈值表登记 **5 档**（轻微/轻/中/重/极重），
而 prompt 只允许模型输出 **3 档**（轻/重/极重）→「轻微」「中」永不产生
→ `_impact_threshold` 返回 `None` → 该损伤**永不进入催促流程**。
实测书 24：最重的「神魂受创（伤势·极重）」**恰恰是唯一不催的那条**，
而轻伤催得最凶 —— **注意力被反向引导**（`memory.py:291-294` 自述）。

本模块用 `@lru_cache` + 模块级断言保证 **`InjuryKind × InjurySeverity` 全组合都有阈值**，
由 `tests/unit/test_injury.py` 强制。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from storyforge.domain.enums import (
    RECOVERY_STATUSES,
    HealthAction,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
)

# --------------------------------------------------------------------------- #
# 弹性恢复阈值表（重建旧项目实测调优的数值）
# --------------------------------------------------------------------------- #

#: 阈值表中的哨兵值：表示"不按章催促"（由伏笔/剧情驱动）。
#: 旧项目用字面量 999 且判定条件写作 `th < 999` —— 能工作但语义模糊。
NEVER_URGE = 999

#: 基础恢复阈值（章）。数值来自旧项目实测调优，语义：
#: 「超过这个章数仍未恢复，就应当安排恢复剧情或明示永久化」。
#:
#: 旧项目原表（`memory.py:_IMPACT_BASE_TH`，仅登记 5 档 × 4 类）：
#:     ("伤势", 轻微/轻/中/重/极重): 1, 1, 2, 3, 5
#:     ("后遗症", ...):              3, 4, 6, 8, 12
#:     ("心魔", ...):                3, 4, 6, 8, 12
#:     ("代价", ""): 12       （缓解/补偿剧情）
#:     ("诅咒", ""): 999      （由解咒伏笔驱动，不按章催）
_BASE_THRESHOLDS: dict[tuple[InjuryKind, InjurySeverity], int] = {
    # 伤势：最快催 —— 轻伤拖久了不合理
    (InjuryKind.WOUND, InjurySeverity.MINOR): 1,
    (InjuryKind.WOUND, InjurySeverity.LIGHT): 1,
    (InjuryKind.WOUND, InjurySeverity.MODERATE): 2,
    (InjuryKind.WOUND, InjurySeverity.SEVERE): 3,
    (InjuryKind.WOUND, InjurySeverity.CRITICAL): 5,
    # 后遗症：慢性，给更长窗口
    (InjuryKind.SEQUELA, InjurySeverity.MINOR): 3,
    (InjuryKind.SEQUELA, InjurySeverity.LIGHT): 4,
    (InjuryKind.SEQUELA, InjurySeverity.MODERATE): 6,
    (InjuryKind.SEQUELA, InjurySeverity.SEVERE): 8,
    (InjuryKind.SEQUELA, InjurySeverity.CRITICAL): 12,
    # 心魔：与后遗症同档
    (InjuryKind.INNER_DEMON, InjurySeverity.MINOR): 3,
    (InjuryKind.INNER_DEMON, InjurySeverity.LIGHT): 4,
    (InjuryKind.INNER_DEMON, InjurySeverity.MODERATE): 6,
    (InjuryKind.INNER_DEMON, InjurySeverity.SEVERE): 8,
    (InjuryKind.INNER_DEMON, InjurySeverity.CRITICAL): 12,
    # 代价：永久·可缓解，靠"补偿剧情"而非"治愈"
    (InjuryKind.COST, InjurySeverity.MINOR): 12,
    (InjuryKind.COST, InjurySeverity.LIGHT): 12,
    (InjuryKind.COST, InjurySeverity.MODERATE): 12,
    (InjuryKind.COST, InjurySeverity.SEVERE): 12,
    (InjuryKind.COST, InjurySeverity.CRITICAL): 12,
    # 诅咒：不按章催，由解咒伏笔驱动
    # ⚠️ 旧项目的缺陷：`("诅咒",""): 999` 配合 `th < 999` 判定 → 永不催促，
    #    而注释说"由解咒伏笔驱动"，**但没有任何代码把解咒伏笔与诅咒关联起来**
    #    （`doc/research/02` §5.4 之三）。本设计用 `bound_foreshadow_key` 建立关联。
    (InjuryKind.CURSE, InjurySeverity.MINOR): NEVER_URGE,
    (InjuryKind.CURSE, InjurySeverity.LIGHT): NEVER_URGE,
    (InjuryKind.CURSE, InjurySeverity.MODERATE): NEVER_URGE,
    (InjuryKind.CURSE, InjurySeverity.SEVERE): NEVER_URGE,
    (InjuryKind.CURSE, InjurySeverity.CRITICAL): NEVER_URGE,
}

#: 穷尽性断言 —— **这是防"最重的伤不催"的关键**
_missing = {
    (k, s) for k in InjuryKind for s in InjurySeverity
} - set(_BASE_THRESHOLDS)
assert not _missing, (
    f"阈值表缺 {sorted((k.value, s.value) for k, s in _missing)} —— "
    f"旧项目正因表只登记部分档位，导致最重的伤反而永不催促（注意力反向引导）"
)

#: 治疗资源关键词（旧项目 `_HEAL_RESOURCE_KWS`）
#: 装备含以下任一词 → 视为「有治疗资源」，恢复窗口 ×0.7（提前）
HEAL_RESOURCE_KEYWORDS: tuple[str, ...] = (
    "药", "丹", "散", "膏", "参", "灵芝", "露", "草", "玉露", "圣水", "灵液",
)

#: 阈值修正因子的边界（旧项目 `max(0.6, min(1.4, f))`）
FACTOR_MIN = 0.6
FACTOR_MAX = 1.4

#: 恢复铺垫的默认提前量（章）—— 解决「恢复太突然」
DEFAULT_PREP_WINDOW = 4


@dataclass(frozen=True)
class ThresholdContext:
    """计算恢复阈值所需的上下文。"""

    #: 是否主角/核心角色（给更长时间）
    is_core: bool = False
    #: 当前持有物（用于判定是否有治疗资源）
    equipment: tuple[str, ...] = ()
    #: 本卷总章数
    volume_total_chapters: int | None = None
    #: 当前章在本卷内的序号（1-based）
    volume_local_index: int | None = None


def base_threshold(kind: InjuryKind, severity: InjurySeverity) -> int:
    """基础恢复阈值（章）。表已保证穷尽，不会返回 None。

    旧项目此处可能返回 `None`（表缺档）→ 该损伤永不进入催促流程。
    本实现**不可能返回 None**（穷尽性由模块级断言保证）。
    """
    return _BASE_THRESHOLDS[(kind, severity)]


def has_heal_resource(equipment: tuple[str, ...] | list[str]) -> bool:
    """是否持有治疗资源。

    ⚠️ 旧项目的实测缺陷：`_fmt_impacts` 读 `ctx["equip"]` 算这个因子，
    但 `_ctx_dmg` 初始化时**没有 `equip` 键**（`memory.py:1178-1186`），
    `limited=True` 路径传的 `dict(ctx or {})` **也没有 equip**
    → **配角永远按「无治疗资源」算，阈值 ×1.3 被推后**。

    本设计：`ThresholdContext.equipment` 是**必填语义**（默认空 tuple），
    由调度器显式传入，不存在"调用方忘了传"的路径。
    """
    joined = "".join(str(x) for x in (equipment or ()))
    return any(k in joined for k in HEAL_RESOURCE_KEYWORDS)


def effective_threshold(
    kind: InjuryKind, severity: InjurySeverity, ctx: ThresholdContext | None = None
) -> int:
    """弹性恢复阈值 = 基础 × 修正因子，clamp 到 [0.6, 1.4]。

    修正因子（旧项目 `_impact_threshold`）：
      · 核心角色      ×1.2   （给更长时间）
      · 有治疗资源    ×0.7   （提前）
      · 无治疗资源    ×1.3   （推后）
      · 卷末最后 20%  ×0.6   （收紧，别把伤拖到下一卷）
    """
    base = base_threshold(kind, severity)
    if base >= NEVER_URGE:
        return base  # 诅咒类：由伏笔驱动，不做章数催促

    c = ctx or ThresholdContext()
    f = 1.0
    if c.is_core:
        f *= 1.2
    f *= 0.7 if has_heal_resource(c.equipment) else 1.3
    if (
        c.volume_total_chapters
        and c.volume_total_chapters > 0
        and c.volume_local_index
        and c.volume_local_index / c.volume_total_chapters >= 0.8
    ):
        f *= 0.6
    f = max(FACTOR_MIN, min(FACTOR_MAX, f))
    return max(1, int(base * f))


# --------------------------------------------------------------------------- #
# 伤势实体
# --------------------------------------------------------------------------- #


@dataclass
class Injury:
    """一条伤势。字段与 `injuries` 表对应（见 `infra/storage/models.py`）。

    ★ 章号一律是**全书递增号**（决策 D-15）。
    旧项目 P0 事故：`since_ch` 写入侧存**卷内号**、消费侧按**全书号**比对
    → `elapsed = 10012 - 1 = 10011` → 阈值仅 1~3 → **每章恒真**
    → 每个损伤每章都挂「本轮必须推进」→ 一章推进不完 7 处
    → Editor 每章判「必推进的损伤未推进」→ **4~5 分 + 必改失败**。
    """

    id: int
    book_id: int
    entity_id: int
    kind: InjuryKind
    name: str
    severity: InjurySeverity
    status: InjuryStatus
    #: 受伤章（全书递增号）
    since_chapter: int
    updated_chapter: int
    #: ★ 规划的恢复章（由 HealthScheduler 计算，不是每章催促）
    expected_heal_chapter: int | None = None
    #: ★ 是否已排入细纲场景 —— False 时**不得**催促（消除旧项目死锁）
    heal_scene_planned: bool = False
    heal_condition: str | None = None
    is_permanent: bool = False
    resolved_chapter: int | None = None
    #: 诅咒类绑定到某个伏笔 key（旧项目声称"由解咒伏笔驱动"但从未实现关联）
    bound_foreshadow_key: str | None = None

    # -- 派生 ---------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """活跃损伤才注入与催促。"""
        return self.status not in (InjuryStatus.HEALED, InjuryStatus.REMOVED)

    @property
    def is_recovering(self) -> bool:
        return self.status in RECOVERY_STATUSES

    @property
    def elapsed(self) -> int:
        """已持续章数。"""
        end = self.resolved_chapter or self.updated_chapter
        return max(0, end - self.since_chapter)

    def chapters_until_expected_heal(self, current_chapter: int) -> int | None:
        if self.expected_heal_chapter is None:
            return None
        return self.expected_heal_chapter - current_chapter

    def urgency(
        self, current_chapter: int, ctx: ThresholdContext | None = None
    ) -> str | None:
        """催促档位。

        ★ **关键修正**：`heal_scene_planned=False` 时**返回 None（不催促）**。
        旧项目在这里无条件催促，而细纲层看不到损伤 → writer 拿到
        「必须推进」却无场景支撑 → 只能忽略 → Editor 判低分 → **死循环**。

        只有在调度器**已经把恢复节拍排进细纲**之后，才允许催促。
        """
        if not self.is_active or self.is_permanent:
            return None
        if not self.heal_scene_planned:
            # 未排入细纲 → 不催促（这是消除死锁的关键）
            return None
        if self.expected_heal_chapter is not None:
            if current_chapter >= self.expected_heal_chapter:
                return "overdue"
            if current_chapter >= self.expected_heal_chapter - 2:
                return "approaching"
            return None
        th = effective_threshold(self.kind, self.severity, ctx)
        if th >= NEVER_URGE:
            return None
        if self.elapsed >= th:
            return "overdue"
        if self.elapsed >= th * 0.8:
            return "approaching"
        return None


# --------------------------------------------------------------------------- #
# 恢复计划与节拍
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HealthBeat:
    """一个恢复节拍 —— **必须指向真实场景**。

    旧项目的恢复是"凭空说一句痊愈了"，本设计要求每个节拍占用一个
    `SceneBeat`（`scene_seq`），使恢复成为**剧情的自然结果**。
    """

    injury_id: int
    action: HealthAction
    chapter_no: int
    #: 指向本章第几个 SceneBeat（0-based）—— 校验其真实存在
    scene_seq: int
    note: str = ""


@dataclass
class HealPlan:
    """一条伤势的恢复计划。"""

    injury_id: int
    entity_id: int
    injury_name: str
    #: 规划的痊愈章
    heal_chapter: int
    #: 预热节拍序列（寻医/求药/疗伤尝试/好转…）
    beats: list[HealthBeat]


def plan_heal(
    injury: Injury,
    *,
    current_chapter: int,
    ctx: ThresholdContext | None = None,
    prep_window: int = DEFAULT_PREP_WINDOW,
) -> HealPlan:
    """为一条伤势规划恢复节拍序列。

    ## 为什么必须有预热序列（解决「恢复太突然」）

    用户痛点原话：「伤势一直不恢复，恢复也需要大纲细节里面补充好，不然太突然」。

    单点"某章突然痊愈"确实突然。因此本函数生成一条**渐进序列**：

        ch 10  新发            左肩剑创（重）
        ch 12  SEEK_TREATMENT  寻医
        ch 14  ACQUIRE_MEDICINE 求药
        ch 16  TREATMENT_ATTEMPT 疗伤（可被打断）
        ch 18  RECOVERING      好转
        ch 20  HEALED          痊愈

    每一步都由调度器写入细纲的 `health_beats`，占用真实 `SceneBeat`。

    ## 节拍数与伤势严重度的关系

    越重的伤，预热越长（有更多"准备"环节），符合叙事直觉。
    """
    th = effective_threshold(injury.kind, injury.severity, ctx)
    if th >= NEVER_URGE or injury.is_permanent:
        # 诅咒（由伏笔驱动）与永久伤残：不规划痊愈，只返回空计划
        return HealPlan(
            injury_id=injury.id,
            entity_id=injury.entity_id,
            injury_name=injury.name,
            heal_chapter=0,
            beats=[],
        )

    # 严重度 → 预热长度：轻微 2 拍，极重 5 拍
    prep_len = {
        InjurySeverity.MINOR: 2,
        InjurySeverity.LIGHT: 2,
        InjurySeverity.MODERATE: 3,
        InjurySeverity.SEVERE: 4,
        InjurySeverity.CRITICAL: 5,
    }[injury.severity]

    # 恢复窗口：优先用 expected_heal_chapter，否则按阈值算
    if injury.expected_heal_chapter is not None:
        heal_chapter = injury.expected_heal_chapter
    else:
        heal_chapter = injury.since_chapter + th

    # 若规划的恢复章已过，则从当前章起顺延（不追溯过去）
    heal_chapter = max(heal_chapter, current_chapter + 2)

    # ★ 预热必须有**足够的跑道**：每个节拍至少占一章。
    # 否则会出现「ch11 求药 / ch11 恶化 / ch12 疗伤 / ch12 好转 / ch13 痊愈」
    # 这种把 5 拍压进 2 章的情况 —— 那不是渐进恢复，仍然是突变。
    # 跑道不足时**顺延恢复章**，而不是压缩节拍。
    total_beats = prep_len + 1  # 预热 + 痊愈
    earliest_heal = current_chapter + 1 + prep_len
    heal_chapter = max(heal_chapter, earliest_heal)

    # 预热节拍从 heal_chapter 往前排，且不早于当前章 + 1
    start = max(current_chapter + 1, heal_chapter - max(prep_len, prep_window))
    sequence = [
        HealthAction.SEEK_TREATMENT,
        HealthAction.ACQUIRE_MEDICINE,
        HealthAction.CONDITION_WORSENS,
        HealthAction.TREATMENT_ATTEMPT,
        HealthAction.RECOVERING,
    ]
    # 取最后 prep_len 个动作，让重伤覆盖更多前置环节
    chosen = sequence[-prep_len:]

    # 节拍章号：在 [start, heal_chapter-1] 上**均匀且互不重复**地铺开
    span = max(prep_len, heal_chapter - start)  # 可用章数
    beats: list[HealthBeat] = []
    for i, action in enumerate(chosen):
        # 优先均匀分布；若章数不足，退化为逐章递增（仍保证唯一）
        if span >= prep_len:
            ch = start + round(i * (heal_chapter - start - 1) / max(1, prep_len - 1))
        else:
            ch = start + i
        beats.append(
            HealthBeat(
                injury_id=injury.id,
                action=action,
                chapter_no=ch,
                scene_seq=0,  # 由细纲规划器填入真实的场景序号
                note=f"{injury.name}：{_ACTION_LABEL[action]}",
            )
        )
    # 去重保护：若仍出现重复章号，逐个顺延（绝不把两拍塞进同一章）
    seen: set[int] = set()
    deduped: list[HealthBeat] = []
    for b in beats:
        ch = b.chapter_no
        while ch in seen or ch >= heal_chapter:
            ch = max(ch, max(seen, default=start - 1) + 1) if seen else ch + 1
            if ch >= heal_chapter:
                ch = heal_chapter - 1
                if ch in seen:
                    break
        seen.add(ch)
        deduped.append(replace(b, chapter_no=ch))
    beats = deduped
    beats.append(
        HealthBeat(
            injury_id=injury.id,
            action=HealthAction.HEALED,
            chapter_no=heal_chapter,
            scene_seq=0,
            note=f"{injury.name}痊愈",
        )
    )
    assert len({b.chapter_no for b in beats}) == len(beats), (
        f"恢复节拍章号必须互不相同（否则等于突变）："
        f"{[(b.chapter_no, b.action.value) for b in beats]}"
    )
    return HealPlan(
        injury_id=injury.id,
        entity_id=injury.entity_id,
        injury_name=injury.name,
        heal_chapter=heal_chapter,
        beats=beats,
    )


_ACTION_LABEL: dict[HealthAction, str] = {
    HealthAction.INFLICTED: "受伤",
    HealthAction.SEEK_TREATMENT: "寻医",
    HealthAction.ACQUIRE_MEDICINE: "求药",
    HealthAction.CONDITION_WORSENS: "伤情恶化",
    HealthAction.TREATMENT_ATTEMPT: "疗伤尝试",
    HealthAction.RECOVERING: "好转",
    HealthAction.HEALED: "痊愈",
    HealthAction.MADE_PERMANENT: "明示永久化",
}


# --------------------------------------------------------------------------- #
# 恢复校验（替代旧项目的关键词自锁）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RecoveryCheck:
    ok: bool
    reason: str


def validate_recovery_claim(
    injury: Injury,
    *,
    target_status: InjuryStatus,
    chapter_no: int,
    beats: list[HealthBeat],
    scene_count: int,
) -> RecoveryCheck:
    """校验一次「恢复申报」是否有结构化证据支撑。

    ## 与旧项目守卫的区别

    旧项目（`memory.py:2114-2121`）要求**同一次 LLM 输出的 events** 里
    同时含角色名 + 25 个治疗语义词之一：

        if any(r in st for r in RECOVERY) and not (
            events_blob and name in events_blob
            and any(k in events_blob for k in RECOVERY_EVENT_KWS)
        ):
            warnings.append("⛔ 一致性守卫：恢复缺治疗/恢复事件支撑，保留原状态")

    守护初衷正确（防"押进城/走进大殿"被当痊愈依据），但副作用是
    **把「记录一致性」变成「叙事义务」**：prompt 只让写 2~5 条关键事件，
    过渡章可能没有 events → **恢复申报被拒 → 自锁**。

    本设计改为**结构化证据**：
      · 必须存在 `HealthAction.HEALED`（或 RECOVERING/TREATMENT_ATTEMPT）的节拍
      · 该节拍的章号必须 == 当前章
      · 该节拍指向的场景必须真实存在（`0 <= scene_seq < scene_count`）
      · 节拍的 injury_id 必须匹配

    好处：确定性（可查表）、无自锁（不依赖自由文本关键词）、
    且能防住旧 bug（无关事件不会被误当痊愈依据）。
    """
    if target_status not in RECOVERY_STATUSES:
        return RecoveryCheck(True, "非恢复类状态，无需校验")

    matching = [
        b for b in beats
        if b.injury_id == injury.id and b.chapter_no == chapter_no
        and b.action in (HealthAction.HEALED, HealthAction.RECOVERING,
                         HealthAction.TREATMENT_ATTEMPT)
    ]
    if not matching:
        return RecoveryCheck(
            False,
            f"{injury.name} 在第{chapter_no}章申报「{target_status.value}」，"
            f"但细纲中没有对应的恢复节拍（HealthBeat）—— 恢复必须由场景承载",
        )
    beat = matching[0]
    if not (0 <= beat.scene_seq < max(1, scene_count)):
        return RecoveryCheck(
            False,
            f"{injury.name} 的恢复节拍指向场景 {beat.scene_seq}，"
            f"但本章只有 {scene_count} 个场景 —— 节拍必须落在真实场景上",
        )
    if target_status == InjuryStatus.HEALED and beat.action != HealthAction.HEALED:
        return RecoveryCheck(
            False,
            f"{injury.name} 申报「痊愈」，但节拍动作是「{_ACTION_LABEL[beat.action]}」"
            f"—— 痊愈需 HEALED 节拍支撑",
        )
    return RecoveryCheck(True, f"第{chapter_no}章由场景 {beat.scene_seq} 承载恢复")


def apply_status_change(
    injury: Injury, new_status: InjuryStatus, *, chapter_no: int
) -> Injury:
    """状态跃迁（返回新对象，**不原地修改** —— 与 LangGraph 不可变约定一致）。"""
    updated = replace(
        injury, status=new_status, updated_chapter=chapter_no
    )
    if new_status in (InjuryStatus.HEALED, InjuryStatus.REMOVED):
        updated = replace(updated, resolved_chapter=chapter_no)
    if new_status in (InjuryStatus.PERMANENT_MITIGABLE, InjuryStatus.BOUND):
        updated = replace(updated, is_permanent=True)
    return updated


__all__ = [
    "NEVER_URGE",
    "HEAL_RESOURCE_KEYWORDS",
    "ThresholdContext",
    "Injury",
    "HealthBeat",
    "HealPlan",
    "RecoveryCheck",
    "base_threshold",
    "effective_threshold",
    "has_heal_resource",
    "plan_heal",
    "validate_recovery_claim",
    "apply_status_change",
]
