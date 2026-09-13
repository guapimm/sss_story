"""实体、关系、归属的领域模型（对策①③，痛点 2 与 4）。

## 旧项目的实测缺陷

### 缺陷 1：势力**不是合法实体类型** → 势力实体 0 个

三套类型词汇表互相冲突：

    entities.py:17   有「组织」，**无「势力」**
    models.py:267    有「势力」
    graph.py:36      **两者都无**

失效链：LLM 吐 `type="势力"` → 判非法 → **静默降级为「其他」** → 图谱再降一次。
实测后果：`relationships` 966 行中 `type_b='势力'` 仅 **7 行**、`'组织'` 14 行。

### 缺陷 2：关系表 86.7% 是**非人际关系**

    人物-物品 256 | 人物-地名 127 | 物品-地名 101 | **人物-人物 98（13.3%）**
    | 物品-物品 32 | 人物-规则 28 | 地名-地名 18 ...

`relation_type` 是**自由文本**：254 条人物-人物边用了 **191 种**标签，
自带 9 词受控词表只覆盖 **31/254 = 12.2%**（`问询者与被问者`6 / `试探与被试探`3 /
`监视`3 …），平均每种只用 **1.33 次** → 系统**无法聚合、比较、要求关系多样性**。

### 缺陷 3：归属用「五元组匹配」且把 `type_b` 当匹配键

`memory.py:2763-2779`：LLM 本次写「组织」下次写「势力」即**匹配失败**
→ 旧边不被解除、新边**继续累积**。实测 `顾长生→执法殿` 同时存在
「归属」与「逃避/即将对峙」两条并存。

### 缺陷 4：动作被当成关系

`post_processor.py` 明文要求「出场实体**全量重报**」+ 17 类端点组合
→ 抽取器被迫把每一次物品接触都写成关系：
`陈砚 <-值守-> 档案馆`、`黄铜条 <-嵌设-> 值夜室`、`第三十八条 <-作保-> 第三十七条`。

## 本模块的对策

| 旧项目 | 本设计 |
|---|---|
| 势力不是合法类型 | `EntityKind` **单一枚举**，`normalize_entity_kind` 未知**抛错不降级** |
| 一张表混装三种语义 | 关系 / 归属 / 状态 / 事件**四分离** |
| 自由文本 `relation_type` | `RelationKind` **枚举** |
| 可累积的边 | 归属带 `joined_chapter`/`left_chapter` **时间区间** |
| 无张力维度 | 新增 `intensity` / `sentiment` / 明暗分离 / **非对称** |
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storyforge.domain.enums import (
    PERMANENT_LOSS_STATES,
    RECOVERABLE_STATES,
    AffiliationLeaveReason,
    EntityKind,
    LifecycleState,
    RelationKind,
    RelationSentiment,
    RelationStatus,
    normalize_entity_kind,
)

# --------------------------------------------------------------------------- #
# 实体
# --------------------------------------------------------------------------- #


@dataclass
class Entity:
    """一等实体 —— 人 / 势力 / 地点 / 物品 / 功法 / 规则 / 术语。

    ## 生命周期用**独立字段**而非名字前缀

    旧项目用 `[毁·可修]青锋剑` 这种前缀表达状态，需要专门的
    `_strip_life_prefix`，且剥离**顺序敏感**：
    `memory.py:668-669` 实证 —— `+[失·不可寻]家庭合影` 若先剥前缀，
    `[失·不可寻]` 会残留并逃过归一化（**误杀**）。

    另外旧项目 `[耗·一次性]` **既不在永久态也不在可恢复态**
    （`memory.py:216-220`），完全失管 —— 消耗品用尽后既不禁止使用、
    也不提示恢复。本设计的 `LifecycleState.CONSUMED` 有明确归类
    （`domain/enums.py` 有模块级断言保证每个状态都被归类）。
    """

    id: int
    book_id: int
    kind: EntityKind
    canonical_name: str
    first_seen_chapter: int
    aliases: list[str] = field(default_factory=list)
    last_seen_chapter: int | None = None
    lifecycle: LifecycleState = LifecycleState.ACTIVE
    attributes: dict = field(default_factory=dict)
    is_active: bool = True

    # -- 派生 ---------------------------------------------------------------

    @property
    def is_permanently_lost(self) -> bool:
        """永久失去 —— 仅可回忆/遗物/旁引，**禁止当作仍在持有**。"""
        return self.lifecycle in PERMANENT_LOSS_STATES

    @property
    def is_recoverable(self) -> bool:
        """可恢复 —— 恢复需**明确剧情过程**，禁止凭空恢复。"""
        return self.lifecycle in RECOVERABLE_STATES

    def matches_name(self, raw: str) -> bool:
        """名字匹配（正名或别名）。

        ⚠️ 旧项目缺陷：`entities.py` 不识别生命周期前缀，
        `[毁·可修]青锋剑` 被**带括号整体注册** → 与无前缀名失配
        → 误判「凭空出现」/重复注册（`OPTIMIZATION_POINTS.md` #3）。
        本设计前缀不存在于名字里，故无需剥离。
        """
        key = raw.strip()
        return key == self.canonical_name or key in self.aliases

    # -- 校验 ---------------------------------------------------------------

    def can_be_used_as_held(self) -> bool:
        """能否当作"仍在持有"使用。

        永久失去的实体不得被当作持有（旧项目规则 5⑤：
        「永久失去名单中的实体仅可通过回忆/遗物/旁引使用」）。
        """
        return not self.is_permanently_lost


def normalize_kind(raw: str) -> EntityKind:
    """归一实体类型。**未知类型抛错而非降级**。

    这是消除旧项目「势力降级为其他」的直接手段。
    """
    return normalize_entity_kind(raw)


# --------------------------------------------------------------------------- #
# 关系（社会关系，含张力维度）
# --------------------------------------------------------------------------- #


@dataclass
class EntityRelation:
    """**社会关系** —— 有情感/立场/权力语义，会演化。

    动作（`查看` / `拾取物品` / `值守`）**不属于关系**，属于 `Event`。
    这是旧项目关系表被污染（86.7% 非人际关系）的直接对策。

    ## 张力维度（解决痛点 4「人物关系单一」）

    旧项目的关系只有一个名词标签（`同门25 / 师徒24 / 盟友21`），
    没有强度、没有双向性、没有明暗张力 → 关系网是**扁平**的。
    """

    id: int
    book_id: int
    from_entity_id: int
    to_entity_id: int
    relation_kind: RelationKind
    since_chapter: int
    updated_chapter: int
    # ── 张力维度 ──
    #: 0~100 强度
    intensity: int = 50
    sentiment: RelationSentiment = RelationSentiment.NEUTRAL
    #: 世人所知的关系表现（旧项目 `visibility=明`）
    surface_facing: str | None = None
    #: 真实立场（旧项目 `visibility=暗`）
    true_facing: str | None = None
    #: ★ 非对称：A→B 与 B→A 是两条独立记录
    is_mutual: bool = True
    status: RelationStatus = RelationStatus.ACTIVE
    changed_by_event_id: int | None = None

    # -- 派生 ---------------------------------------------------------------

    @property
    def has_hidden_tension(self) -> bool:
        """有暗线张力（表面立场 ≠ 真实立场）。

        旧项目只有 `visibility=明/暗` 一个布尔量，且「暗线关系无自动转明」
        被列为长期开放项（`memory.py:2667` 只在同端点新报「明」时同步）。
        """
        if self.true_facing is None:
            return False
        return (self.surface_facing or "") != (self.true_facing or "")

    @property
    def needs_counterpart(self) -> bool:
        """是否需要一条反向记录（非对称关系）。"""
        return not self.is_mutual

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.from_entity_id == self.to_entity_id:
            problems.append("关系的两端不得是同一实体")
        if not (0 <= self.intensity <= 100):
            problems.append(f"intensity 必须在 0~100，实测 {self.intensity}")
        if self.updated_chapter < self.since_chapter:
            problems.append(
                f"updated_chapter({self.updated_chapter}) 早于 "
                f"since_chapter({self.since_chapter})"
            )
        return problems


# --------------------------------------------------------------------------- #
# 归属（带时间区间，非可累积的边）
# --------------------------------------------------------------------------- #


@dataclass
class Affiliation:
    """**归属** —— 成员关系，有明确时间区间。

    ## 为什么不走关系表

    旧项目的失效链（`doc/research/00` §12bis.4）：
      1. 归属写在 `relationships` 表里
      2. 更新逻辑用「五元组匹配」，**把 `type_b` 当匹配键**
         （`memory.py:2763-2779`）
      3. LLM 本次写「组织」、下次写「势力」→ **匹配失败**
      4. 旧边**不被解除**、新边**继续累积**
      5. 实测 `顾长生→执法殿` 同时存在「归属」与「逃避/即将对峙」

    另外 `memory.py:2802-2817` 会**盲目把「不同 relation_type 的旧边」
    全部置「解除」** → 「归属」vs「所属」（同义异写）**互相删除**。

    本设计：归属有 `joined_chapter` / `left_chapter` 时间区间，
    天然表达"何时加入、何时离开"，**不需要"解除旧边"这个操作**。
    """

    id: int
    book_id: int
    entity_id: int
    faction_id: int
    joined_chapter: int
    left_chapter: int | None = None
    role: str | None = None
    leave_reason: AffiliationLeaveReason | None = None

    def is_active_at(self, chapter_no: int) -> bool:
        """某章时是否仍在籍。"""
        if chapter_no < self.joined_chapter:
            return False
        return self.left_chapter is None or chapter_no < self.left_chapter

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.left_chapter is not None and self.left_chapter < self.joined_chapter:
            problems.append(
                f"left_chapter({self.left_chapter}) 早于 "
                f"joined_chapter({self.joined_chapter})"
            )
        if self.left_chapter is not None and self.leave_reason is None:
            problems.append("离开必须有 leave_reason（叛变/脱离/被逐/覆灭）")
        return problems


# --------------------------------------------------------------------------- #
# 关系网络健康度（痛点 4 的量化验收）
# --------------------------------------------------------------------------- #


@dataclass
class RelationNetworkStats:
    """关系网络统计 —— 用于验收「人物关系单一」是否改善。

    旧项目实测基线（966 条关系）：
      · 人物-人物占比 **13.3%**
      · 254 条人物-人物边用了 **191 种**标签
      · 被追踪角色仅 **18** 个（400 章）
    """

    total: int = 0
    person_person: int = 0
    with_hidden_tension: int = 0
    non_mutual: int = 0
    distinct_kinds: int = 0
    #: 关系类型分布（用于算熵）
    kind_counts: dict[str, int] = field(default_factory=dict)

    @property
    def person_person_ratio(self) -> float:
        """人物-人物关系占比。旧项目 **13.3%**，目标 ≥60%。"""
        return self.person_person / self.total if self.total else 0.0

    @property
    def tension_ratio(self) -> float:
        """有暗线张力的关系占比。目标 15%~40%（太低=没暗线，太高=人人两面）。"""
        return self.with_hidden_tension / self.total if self.total else 0.0

    @property
    def non_mutual_ratio(self) -> float:
        """非对称关系占比。目标 ≥10%。"""
        return self.non_mutual / self.total if self.total else 0.0

    @property
    def kind_entropy(self) -> float:
        """关系类型分布熵（越高越多样）。

        旧项目 191 种标签但平均只用 1.33 次 → 名义熵高、**实际不可用**
        （因为无法聚合）。本设计用**枚举**，熵才有意义。
        """
        import math

        if not self.kind_counts or self.total == 0:
            return 0.0
        ent = 0.0
        for n in self.kind_counts.values():
            p = n / self.total
            if p > 0:
                ent -= p * math.log2(p)
        return ent


def analyze_relations(
    relations: list[EntityRelation],
    *,
    is_person: dict[int, bool] | None = None,
) -> RelationNetworkStats:
    """统计关系网络健康度。

    `is_person` 把实体 ID 映射到"是否人物"；缺省时只统计总数与张力。
    """
    stats = RelationNetworkStats(total=len(relations))
    person = is_person or {}
    for r in relations:
        if person.get(r.from_entity_id) and person.get(r.to_entity_id):
            stats.person_person += 1
        if r.has_hidden_tension:
            stats.with_hidden_tension += 1
        if not r.is_mutual:
            stats.non_mutual += 1
        key = r.relation_kind.value
        stats.kind_counts[key] = stats.kind_counts.get(key, 0) + 1
    stats.distinct_kinds = len(stats.kind_counts)
    return stats


__all__ = [
    "Entity",
    "EntityRelation",
    "Affiliation",
    "RelationNetworkStats",
    "normalize_kind",
    "analyze_relations",
]
