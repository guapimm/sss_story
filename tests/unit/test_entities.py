"""实体、关系、归属测试（对策①③ / 痛点 2 与 4）。

★ 验收核心：旧项目的坏模式必须被**结构性阻止**，而不只是"不推荐"。
"""

from __future__ import annotations

import pytest

from storyforge.domain.entities import (
    Affiliation,
    Entity,
    EntityRelation,
    analyze_relations,
    normalize_kind,
)
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

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# ★ 势力必须是合法类型（旧项目势力实体 0 个的根因）
# --------------------------------------------------------------------------- #


def test_faction_is_a_valid_entity_kind() -> None:
    """★「势力」必须是合法枚举值。

    旧项目 `entities.py:17` 的 `ENTITY_TYPES` **没有「势力」**，
    而 prompt 反复要求输出 `type=势力` → 判定非法 → **静默降级「其他」**
    → `type_b='势力'` 在 966 条关系里只剩 **7 条**。
    """
    assert EntityKind.FACTION.value == "势力"
    assert normalize_kind("势力") is EntityKind.FACTION
    assert normalize_kind("组织") is EntityKind.ORGANIZATION


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("势力", EntityKind.FACTION),
        ("宗门", EntityKind.FACTION),
        ("家族", EntityKind.FACTION),
        ("门派", EntityKind.FACTION),
        ("组织", EntityKind.ORGANIZATION),
        ("机构", EntityKind.ORGANIZATION),
        ("地名", EntityKind.LOCATION),
        ("地点", EntityKind.LOCATION),
        ("角色", EntityKind.CHARACTER),
        ("道具", EntityKind.ITEM),
        ("装备", EntityKind.ITEM),
        ("武学", EntityKind.TECHNIQUE),
    ],
)
def test_entity_kind_aliases_normalize(raw: str, expected: EntityKind) -> None:
    """旧项目/模型的各种写法必须归一，而不是被判非法后降级。"""
    assert normalize_kind(raw) is expected


def test_unknown_entity_kind_raises_instead_of_downgrading() -> None:
    """★ 未知类型必须**抛错**，不得静默降级为「其他」。

    这条断言直接对应旧项目的失效链：
        LLM 吐 type="势力" → 判非法 → 降级「其他」→ 图谱再降一次
    「降级」使问题**不可见** —— 数据看起来没坏，但势力体系消失了。
    """
    with pytest.raises(ValueError, match="未登记"):
        normalize_kind("莫须有的类型")
    with pytest.raises(ValueError):
        normalize_kind("")


def test_organization_and_faction_are_both_reachable() -> None:
    """「势力」与「组织」都可达 —— 消除三套词汇表冲突。

    旧项目三套词汇表：`entities.py` 有组织无势力、`models.py` 有势力、
    `graph.py` 两者都无。本设计统一在 `EntityKind`。
    """
    kinds = {k.value for k in EntityKind}
    assert "势力" in kinds
    assert "组织" in kinds


# --------------------------------------------------------------------------- #
# 生命周期（替代名字前缀）
# --------------------------------------------------------------------------- #


def test_every_lifecycle_state_is_classified() -> None:
    """★ 每个生命周期状态必须归入「永久失去」「可恢复」或「active」之一。

    旧项目 `[耗·一次性]` **既不在 `_PERMANENT_STATES` 也不在
    `_RECOVERABLE_STATES`**（`memory.py:216-220`）→ 完全失管：
    消耗品用尽后既不禁止使用、也不提示恢复。
    """
    all_states = set(LifecycleState)
    classified = PERMANENT_LOSS_STATES | RECOVERABLE_STATES | {LifecycleState.ACTIVE}
    assert classified == all_states, (
        f"未归类：{all_states - classified}（旧项目 [耗·一次性] 正是这样失管的）"
    )


def test_consumed_is_permanent_loss() -> None:
    """`[耗·一次性]` 必须是永久失去 —— 用尽的丹药不能再被"使用"。"""
    assert LifecycleState.CONSUMED in PERMANENT_LOSS_STATES


def test_permanently_lost_entity_cannot_be_held() -> None:
    e = Entity(
        id=1, book_id=1, kind=EntityKind.ITEM, canonical_name="青锋剑",
        first_seen_chapter=1, lifecycle=LifecycleState.DESTROYED_PERMANENT,
    )
    assert e.is_permanently_lost
    assert not e.can_be_used_as_held()


def test_recoverable_entity_is_not_permanent_loss() -> None:
    e = Entity(
        id=1, book_id=1, kind=EntityKind.ITEM, canonical_name="青锋剑",
        first_seen_chapter=1, lifecycle=LifecycleState.DESTROYED_REPAIRABLE,
    )
    assert e.is_recoverable and not e.is_permanently_lost
    assert e.can_be_used_as_held(), "可修复的剑仍属于主角（只是当前损坏）"


def test_entity_name_matching_with_aliases() -> None:
    """别名匹配 —— 旧项目需要专门写 `_strip_life_prefix` 才能匹配。

    并且旧项目的剥离**顺序敏感**（`memory.py:668-669`）：
    `+[失·不可寻]家庭合影` 若先剥前缀会残留 `[失·不可寻]` 逃过归一化（误杀）。
    本设计前缀不在名字里，无顺序依赖。
    """
    e = Entity(
        id=1, book_id=1, kind=EntityKind.CHARACTER, canonical_name="陈砚",
        aliases=["小砚", "砚哥"], first_seen_chapter=1,
    )
    assert e.matches_name("陈砚")
    assert e.matches_name("小砚")
    assert not e.matches_name("老周")


# --------------------------------------------------------------------------- #
# 关系：自由文本 → 枚举（旧项目 191 种标签无法聚合）
# --------------------------------------------------------------------------- #


def test_relation_kinds_are_enumerated() -> None:
    """★ 关系类型必须是枚举。

    旧项目实测：254 条人物-人物边用了 **191 种**自由文本标签，
    自带 9 词受控词表只覆盖 **31/254 = 12.2%**
    → 系统无法回答"敌对关系到底几条"（`敌对/对手/仇视/对立` 无法归一）。

    ★ 关键：旧项目那 9 词受控词表必须**全部**能在本枚举里找到 ——
    否则重写后又会出现"某些关系无处归类"的降级问题。
    """
    kinds = {k.value for k in RelationKind}
    old_vocab = ("师徒", "敌对", "盟友", "同门", "结拜", "血亲", "道侣", "仇敌", "上下级")
    missing = [v for v in old_vocab if v not in kinds]
    assert not missing, f"缺少旧项目受控词表的核心关系：{missing}"
    assert len(kinds) < 30, "关系类型必须是有限枚举，不能是自由文本"


def test_relation_actions_are_not_relation_kinds() -> None:
    """★ 动作**不是**关系 —— 旧项目关系表 86.7% 被动作污染。

    实测污染样本：`陈砚 <-值守-> 档案馆`、`查看`、`记录`、`离开`、
    `拾取物品`、`黄铜条 <-嵌设-> 值夜室`。
    这些属于 `Event`，不属于 `EntityRelation`。
    """
    kinds = {k.value for k in RelationKind}
    for action in ("查看", "记录", "离开", "拾取物品", "值班", "值守", "嵌设", "持有"):
        assert action not in kinds, f"动作「{action}」不得成为关系类型"


def test_relation_tension_dimensions() -> None:
    """张力维度（旧项目只有名词标签，无强度/无双向性/无明暗）。"""
    r = EntityRelation(
        id=1, book_id=1, from_entity_id=1, to_entity_id=2,
        relation_kind=RelationKind.ALLY, since_chapter=1, updated_chapter=5,
        intensity=70, sentiment=RelationSentiment.CONFLICTED,
        surface_facing="表面盟友", true_facing="暗中提防",
    )
    assert r.has_hidden_tension
    assert r.validate() == []


def test_relation_without_true_facing_has_no_tension() -> None:
    r = EntityRelation(
        id=1, book_id=1, from_entity_id=1, to_entity_id=2,
        relation_kind=RelationKind.BLOOD, since_chapter=1, updated_chapter=1,
        surface_facing="兄弟",
    )
    assert not r.has_hidden_tension


def test_non_mutual_relation_needs_counterpart() -> None:
    """非对称关系 —— 旧模型完全无法表达「A 信任 B 但 B 在利用 A」。"""
    r = EntityRelation(
        id=1, book_id=1, from_entity_id=1, to_entity_id=2,
        relation_kind=RelationKind.ALLY, since_chapter=1, updated_chapter=1,
        is_mutual=False, intensity=80, sentiment=RelationSentiment.POSITIVE,
    )
    assert r.needs_counterpart


def test_relation_validation_catches_problems() -> None:
    bad = EntityRelation(
        id=1, book_id=1, from_entity_id=1, to_entity_id=1,  # 自环
        relation_kind=RelationKind.ENEMY, since_chapter=5, updated_chapter=1,
        intensity=150,  # 越界
    )
    problems = bad.validate()
    assert any("同一实体" in p for p in problems)
    assert any("0~100" in p for p in problems)
    assert any("updated_chapter" in p for p in problems)


# --------------------------------------------------------------------------- #
# 归属：时间区间（旧项目用可累积的边）
# --------------------------------------------------------------------------- #


def test_affiliation_is_time_scoped() -> None:
    """★ 归属有明确时间区间 —— 不需要"解除旧边"这个易错操作。

    旧项目失效链：五元组匹配 + 把 `type_b` 当键
    → LLM 写「组织」/「势力」不一致 → 匹配失败 → 旧边不解除、新边累积。
    """
    a = Affiliation(
        id=1, book_id=1, entity_id=1, faction_id=10,
        joined_chapter=5, left_chapter=20,
        leave_reason=AffiliationLeaveReason.DEFECTED,
    )
    assert not a.is_active_at(4)
    assert a.is_active_at(5)
    assert a.is_active_at(19)
    assert not a.is_active_at(20)
    assert a.validate() == []


def test_affiliation_ongoing_has_no_left_chapter() -> None:
    a = Affiliation(id=1, book_id=1, entity_id=1, faction_id=10, joined_chapter=5)
    assert a.is_active_at(9999)


def test_affiliation_validation_catches_bad_ranges() -> None:
    bad = Affiliation(
        id=1, book_id=1, entity_id=1, faction_id=10,
        joined_chapter=20, left_chapter=5,  # 离开早于加入
    )
    problems = bad.validate()
    assert any("left_chapter" in p for p in problems)
    assert any("leave_reason" in p for p in problems), "离开必须说明原因"


# --------------------------------------------------------------------------- #
# 关系网络健康度（痛点 4 的量化验收）
# --------------------------------------------------------------------------- #


def _mk(i: int, a: int, b: int, kind: RelationKind, **kw) -> EntityRelation:
    return EntityRelation(
        id=i, book_id=1, from_entity_id=a, to_entity_id=b,
        relation_kind=kind, since_chapter=1, updated_chapter=1, **kw
    )


def test_relation_stats_computes_person_person_ratio() -> None:
    """人物-人物占比 —— 旧项目 **13.3%**，目标 ≥60%。"""
    rels = [
        _mk(1, 1, 2, RelationKind.MASTER_APPRENTICE),
        _mk(2, 1, 3, RelationKind.BLOOD),
        _mk(3, 2, 3, RelationKind.ALLY),
        _mk(4, 1, 100, RelationKind.MEMBER),     # 人物-势力
        _mk(5, 1, 200, RelationKind.DEBT),       # 人物-物品（这里简化）
    ]
    is_person = {1: True, 2: True, 3: True, 100: False, 200: False}
    stats = analyze_relations(rels, is_person=is_person)
    assert stats.total == 5
    assert stats.person_person == 3
    assert stats.person_person_ratio == pytest.approx(0.6)


def test_relation_stats_counts_tension_and_asymmetry() -> None:
    rels = [
        _mk(1, 1, 2, RelationKind.ALLY,
            surface_facing="盟友", true_facing="暗中提防"),
        _mk(2, 1, 3, RelationKind.BLOOD, surface_facing="兄弟"),
        _mk(3, 2, 3, RelationKind.RIVAL, is_mutual=False),
    ]
    stats = analyze_relations(rels)
    assert stats.with_hidden_tension == 1
    assert stats.tension_ratio == pytest.approx(1 / 3)
    assert stats.non_mutual == 1
    assert stats.non_mutual_ratio == pytest.approx(1 / 3)


def test_relation_entropy_rewards_diversity() -> None:
    """关系类型熵 —— 旧项目 191 种标签但均值 1.33 次，熵高却不可用。

    本设计用枚举，熵才有意义（可聚合、可比较）。
    """
    uniform = [_mk(i, 1, i + 2, k) for i, k in enumerate(RelationKind, start=1)]
    stats = analyze_relations(uniform)
    assert stats.distinct_kinds == len(list(RelationKind))
    assert stats.kind_entropy > 3.5

    monotone = [_mk(1, 1, 2, RelationKind.ALLY)] * 10
    mono_stats = analyze_relations(monotone)
    assert mono_stats.kind_entropy == pytest.approx(0.0)


def test_relation_stats_handles_empty() -> None:
    stats = analyze_relations([])
    assert stats.total == 0
    assert stats.person_person_ratio == 0.0
    assert stats.kind_entropy == 0.0


def test_relation_status_can_be_broken() -> None:
    r = _mk(1, 1, 2, RelationKind.ENEMY)
    r.status = RelationStatus.BROKEN
    assert r.status == RelationStatus.BROKEN


# --------------------------------------------------------------------------- #
# 与旧项目坏数据的对照：本设计能表达旧项目表达不了的东西
# --------------------------------------------------------------------------- #


def test_can_express_asymmetric_trust_like_real_fiction() -> None:
    """旧项目模型无法表达「陈砚信任老周，老周在利用陈砚」。

    旧项目只有一条无向边 + 一个名词标签 + 一个 `visibility` 布尔量。
    """
    trust = EntityRelation(
        id=1, book_id=1, from_entity_id=1, to_entity_id=2,  # 陈砚 → 老周
        relation_kind=RelationKind.ALLY, since_chapter=1, updated_chapter=10,
        intensity=80, sentiment=RelationSentiment.POSITIVE,
        surface_facing="前辈与后辈", true_facing="真心信任",
        is_mutual=False,
    )
    exploit = EntityRelation(
        id=2, book_id=1, from_entity_id=2, to_entity_id=1,  # 老周 → 陈砚
        relation_kind=RelationKind.ALLY, since_chapter=1, updated_chapter=10,
        intensity=30, sentiment=RelationSentiment.NEGATIVE,
        surface_facing="前辈与后辈", true_facing="把他当棋子",
        is_mutual=False,
    )
    # 两条同 kind 的关系方向相反、情感相反 —— 旧项目的无向边表达不了
    assert trust.sentiment != exploit.sentiment
    assert trust.true_facing != exploit.true_facing
    assert trust.needs_counterpart and exploit.needs_counterpart
    # 反过来显式表达「没有隐藏立场」
    honest = EntityRelation(
        id=3, book_id=1, from_entity_id=1, to_entity_id=3,
        relation_kind=RelationKind.SWORN, since_chapter=1, updated_chapter=1,
        surface_facing="结拜兄弟", true_facing="结拜兄弟",
    )
    assert honest.has_hidden_tension is False


def test_faction_entity_can_carry_power_attributes() -> None:
    """势力是一等实体，可携带强弱/层级属性（旧项目完全没有）。"""
    f = Entity(
        id=10, book_id=1, kind=EntityKind.FACTION, canonical_name="七夜社",
        first_seen_chapter=30,
        attributes={"tier": "顶级", "power_level": 80, "resources": ["人工规则"]},
    )
    assert f.kind is EntityKind.FACTION
    assert f.attributes["power_level"] == 80
