"""仓储层测试 —— 每条防一个**实测复现过的**缺陷。

## 本文件防的是什么

1. **`books` 是死表** —— `Book.mode` 从未被写/读，全仓无创建 `Book` 行的
   代码。而 `injuries`/`entities` 都 FK 到 `books.id`，
   **没有 Book 行任何子表都插不进去**（`PRAGMA foreign_keys=ON`）。
2. **`injuries` 表零写入路径** —— 于是 `HealthScheduler` 永远拿到空列表，
   `health_beats_injected` 恒 0（痛点 1「伤势永不恢复」的落地环断裂）。
3. **`since_chapter` 被每次抽取重置** —— 旧项目 `since_ch` 写入/消费口径
   不一致导致阈值判定**每章恒真**。本层断言"更新不重置 since_chapter"。
4. **`resolved_chapter` 不写** —— 它是恢复率的**唯一证据**。
   旧项目 36 条损伤里 `status` 从未出现「痊愈」→ 恢复率实测 **0%**。
"""

from __future__ import annotations

import pytest

from storyforge.domain.enums import (
    BookMode,
    EntityKind,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
)
from storyforge.infra.storage.repositories import (
    BookRepository,
    EntityRepository,
    InjuryRepository,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 夹具：一条完整的 FK 链（books → entities → injuries）
# --------------------------------------------------------------------------- #


@pytest.fixture
def books(db) -> BookRepository:
    return BookRepository(db)


@pytest.fixture
def entities(db) -> EntityRepository:
    return EntityRepository(db)


@pytest.fixture
def injuries(db) -> InjuryRepository:
    return InjuryRepository(db)


@pytest.fixture
def book(books: BookRepository):
    return books.upsert(
        book_id=1, topic="规则怪谈", genre="suspense",
        mode=BookMode.STORY, target_chapters=100,
    )


# --------------------------------------------------------------------------- #
# BookRepository —— 让死表复活
# --------------------------------------------------------------------------- #


def test_book_is_actually_created(books: BookRepository) -> None:
    """★ `books` 表此前是**死表**（全仓无写入路径）。

    它是 FK 根：`injuries`/`entities`/`chapters` 都引用 `books.id`，
    所以"建不了 Book"等于"任何子表都写不进去"。
    """
    assert books.count() == 0
    rec = books.upsert(book_id=7, topic="修仙", genre="xianxia")
    assert books.count() == 1
    assert rec.id == 7
    assert books.find_by_id(7) is not None
    assert books.find_by_id(999) is None


def test_book_mode_is_persisted(books: BookRepository) -> None:
    """★ `Book.mode` 此前**只写不读**且从未落库 —— 模式实际上无归属。

    story / learn 的唯一持久化归属就是这一列。
    """
    books.upsert(book_id=1, topic="t", genre="g", mode=BookMode.STORY)
    books.upsert(book_id=2, topic="t", genre="g", mode=BookMode.LEARN)
    assert books.find_by_id(1).mode is BookMode.STORY
    assert books.find_by_id(2).mode is BookMode.LEARN
    assert books.count() == 2


def test_book_repo_has_no_mode_dimension_query() -> None:
    """★ 仓储**不得**提供按模式筛选的查询（决策 D-08 + 契约规则 3）。

    实测：初版写了 `list_by_mode()`，被规则 3 当场拦下
    （`BookRow.mode == mode.value` 是模式分支）。
    规则 3 的白名单是**空集** —— 正确处理是**不加这个 API**，
    而不是给规则开例外。需要按模式筛选时由模式能力层组装条件。
    """
    assert not hasattr(BookRepository, "list_by_mode"), (
        "不得在共享仓储里判模式（契约规则 3 会拦下，且 D-08 要求经 modes/ Protocol）"
    )


def test_book_upsert_is_idempotent_and_preserves_progress(
    books: BookRepository, book
) -> None:
    """重复 `upsert` 不得重置进度 —— 进度是流水线的成果。

    旧项目 `books.bible` 列在重开生成时被清空
    → 每章 L0 圣经**静默为空**、世界观约束失效。同类风险。
    """
    books.set_progress(1, chapter_no=42, volume_no=3)
    again = books.upsert(book_id=1, topic="改过的主题", genre="suspense")
    assert again.current_chapter == 42, "重开生成不得重置进度"
    assert again.current_volume == 3
    assert again.topic == "改过的主题", "topic 应当更新"


def test_book_set_progress_raises_on_missing(books: BookRepository) -> None:
    with pytest.raises(KeyError, match="不存在"):
        books.set_progress(404, chapter_no=1)


# --------------------------------------------------------------------------- #
# EntityRepository
# --------------------------------------------------------------------------- #


def test_entity_get_or_create_is_idempotent(entities: EntityRepository, book) -> None:
    a = entities.get_or_create(book_id=1, name="林雪", kind=EntityKind.CHARACTER)
    b = entities.get_or_create(book_id=1, name="林雪", kind=EntityKind.CHARACTER)
    assert a.id == b.id, "同名同 kind 不得建两行（唯一约束 book_id+kind+name）"
    assert entities.count() == 1


def test_entity_same_name_different_kind_is_two_rows(
    entities: EntityRepository, book
) -> None:
    """唯一约束是 `(book_id, kind, canonical_name)` —— 同名不同 kind 是两条。

    与 `models.py:245` 的约束口径一致（不是"同名即同实体"）。
    """
    entities.get_or_create(book_id=1, name="青云", kind=EntityKind.CHARACTER)
    entities.get_or_create(book_id=1, name="青云", kind=EntityKind.FACTION)
    assert entities.count() == 2


def test_entity_kind_alias_is_normalized(entities: EntityRepository, book) -> None:
    """★ `宗门`/`门派`/`家族` 必须归一到 `FACTION`。

    旧项目三套类型词汇表冲突 → LLM 吐 `type="势力"` 判非法 → 降级「其他」
    → 实测 966 条关系里势力仅 7 条、**势力实体 0 个**。
    """
    for alias in ("宗门", "门派", "家族", "国家"):
        rec = entities.get_or_create(book_id=1, name=f"某{ alias }", kind=alias)
        assert rec.kind is EntityKind.FACTION, f"{alias} 未归一到 FACTION"


def test_entity_unknown_kind_raises_not_silently_degrades(
    entities: EntityRepository, book
) -> None:
    """★ 未知类型必须**抛错**，不得静默降级为 `OTHER`。

    静默降级正是旧项目势力实体归零的那条路径（`normalize_entity_kind`
    的 docstring 明确记载）。
    """
    with pytest.raises(ValueError, match="未登记的实体类型"):
        entities.get_or_create(book_id=1, name="怪东西", kind="不存在的类型")


def test_entity_find_by_alias(entities: EntityRepository, book) -> None:
    """别名命中必须返回同一实体（否则"一人两名"会被建成两行）。"""
    rec = entities.get_or_create(book_id=1, name="林雪", kind=EntityKind.CHARACTER)
    with entities.session() as sess:
        from storyforge.infra.storage.models import Entity as Row
        row = sess.get(Row, rec.id)
        row.aliases = '["小雪", "雪丫头"]'
    assert entities.find_by_name(1, "小雪").id == rec.id
    assert entities.find_by_name(1, "雪丫头").id == rec.id
    assert entities.find_by_name(1, "不存在的人") is None


def test_entity_broken_aliases_json_does_not_crash(
    entities: EntityRepository, book
) -> None:
    """坏 JSON 不得让整本书读不出来（旧项目在这里会直接抛）。"""
    rec = entities.get_or_create(book_id=1, name="林雪", kind=EntityKind.CHARACTER)
    with entities.session() as sess:
        from storyforge.infra.storage.models import Entity as Row
        sess.get(Row, rec.id).aliases = "{不是合法 JSON"
    got = entities.find_by_name(1, "林雪")
    assert got is not None and got.aliases == []


def test_entity_empty_name_rejected(entities: EntityRepository, book) -> None:
    with pytest.raises(ValueError, match="实体名不得为空"):
        entities.get_or_create(book_id=1, name="   ")


# --------------------------------------------------------------------------- #
# InjuryRepository —— 痛点 1 的落地环
# --------------------------------------------------------------------------- #


def test_injury_can_actually_be_persisted(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    """★★ 这是**痛点 1 的核心断言**：伤势此前**零生产写入路径**。

    `injuries` 表、`HealthScheduler`、`attach_health_beats` 全都实现了，
    但没有任何代码写一行伤势 → 调度器永远拿到空列表
    → `health_beats_injected` 恒 0 →「恢复率 0%」的落地环断裂。
    """
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    assert injuries.list_active(1) == []

    inj = injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.MODERATE,
        status=InjuryStatus.NEW, chapter_no=5,
    )
    assert inj.id > 0
    active = injuries.list_active(1)
    assert len(active) == 1, "伤势必须真的落库，否则恢复链路没有输入"
    assert active[0].name == "左肩剑创"
    assert active[0].since_chapter == 5
    assert active[0].is_active is True


def test_injury_fk_is_enforced(
    injuries: InjuryRepository, book
) -> None:
    """★ 实体不存在时插入必须被**外键拒绝**。

    SQLite 默认不强制外键 —— 本项目的 `db.py:42` 显式开启
    `PRAGMA foreign_keys=ON`。此测试证明它真的生效：
    否则会出现"伤势指向一个不存在的实体"的孤儿行。
    """
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        injuries.upsert(
            book_id=1, entity_id=99999, name="孤儿伤",
            kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
            status=InjuryStatus.NEW, chapter_no=1,
        )


def test_injury_upsert_does_not_reset_since_chapter(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    """★ 更新**不得**重置 `since_chapter`（"伤了多久"是恢复判定的输入）。

    旧项目 `since_ch` 写入侧存卷内号、消费侧按全书号比对
    → `elapsed = 10012 - 1 = 10011` → 阈值仅 1~3 → **每章恒真**
    → 每个损伤每章都挂「本轮必须推进」→ Editor 每章判「未推进」。
    """
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    first = injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.MODERATE,
        status=InjuryStatus.NEW, chapter_no=5,
    )
    # 第 9 章复诊：状态变了，但受伤章不能变
    later = injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
        status=InjuryStatus.IMPROVING, chapter_no=9,
    )
    assert later.id == first.id, "同名同实体的伤势应更新而非新建"
    assert later.since_chapter == 5, "since_chapter 被重置了（每章恒真的根因）"
    assert later.updated_chapter == 9
    assert later.severity is InjurySeverity.LIGHT
    assert injuries.count() == 1


def test_injury_healed_sets_resolved_chapter(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    """★ `resolved_chapter` 是**恢复率的唯一证据**。

    旧项目 36 条损伤里 `status` 从未出现「痊愈」「解除」
    → 恢复率实测 **0%**。没有 resolved_chapter 就无法计算恢复率。
    """
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.MODERATE,
        status=InjuryStatus.NEW, chapter_no=5,
    )
    healed = injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.MINOR,
        status=InjuryStatus.HEALED, chapter_no=17,
    )
    assert healed.status is InjuryStatus.HEALED
    assert healed.resolved_chapter == 17
    assert healed.is_active is False
    # 痊愈后不再出现在活跃列表里（调度器不该再催它）
    assert injuries.list_active(1) == []


def test_injury_resolved_chapter_is_not_overwritten(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    """已记录的恢复章不得被后续更新改写（`row.resolved_chapter is None` 守卫）。"""
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    injuries.upsert(book_id=1, entity_id=hero.id, name="伤",
                    kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
                    status=InjuryStatus.HEALED, chapter_no=10)
    again = injuries.upsert(book_id=1, entity_id=hero.id, name="伤",
                            kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
                            status=InjuryStatus.HEALED, chapter_no=30)
    assert again.resolved_chapter == 10, "恢复章被后续更新改写了"


def test_injury_heal_scene_planned_flag(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    """★ `heal_scene_planned=True` 才允许催促 —— 消除旧项目**死锁**。

    旧项目无条件催促，而细纲层看不到损伤 → writer 拿到「必须推进」
    却无场景支撑 → 只能忽略 → Editor 判低分 → 死循环。
    """
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    inj = injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.SEVERE,
        status=InjuryStatus.NEW, chapter_no=5,
    )
    assert inj.heal_scene_planned is False, "未排入细纲前不得标记已规划"
    assert inj.urgency(6) is None, "未排场景时 urgency 必须为 None（不催促）"

    injuries.mark_heal_planned(inj.id, expected_heal_chapter=12)
    after = injuries.list_active(1)[0]
    assert after.heal_scene_planned is True
    assert after.expected_heal_chapter == 12


def test_injury_mark_heal_planned_missing_raises(
    injuries: InjuryRepository,
) -> None:
    with pytest.raises(KeyError, match="不存在"):
        injuries.mark_heal_planned(404, expected_heal_chapter=10)


def test_injury_empty_name_rejected(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    with pytest.raises(ValueError, match="伤势名不得为空"):
        injuries.upsert(book_id=1, entity_id=hero.id, name="  ",
                        kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
                        status=InjuryStatus.NEW, chapter_no=1)


def test_injury_find_by_entity(
    injuries: InjuryRepository, entities: EntityRepository, book
) -> None:
    a = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    b = entities.get_or_create(book_id=1, name="林雪", kind=EntityKind.CHARACTER)
    for name, eid in (("左肩剑创", a.id), ("内腑震伤", a.id), ("腕骨裂", b.id)):
        injuries.upsert(book_id=1, entity_id=eid, name=name,
                        kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
                        status=InjuryStatus.NEW, chapter_no=5)
    assert len(injuries.find_by_entity(1, a.id)) == 2
    assert len(injuries.find_by_entity(1, b.id)) == 1
    assert len(injuries.list_for_book(1)) == 3


def test_injury_rows_are_scoped_per_book(
    books: BookRepository, injuries: InjuryRepository, entities: EntityRepository
) -> None:
    """★ 跨书隔离 —— 旧项目删书靠人工清单（`BOOK_SCOPED_MODEL_NAMES`
    自承历史上漏过 6 张表并产生过孤儿行）。"""
    for bid in (1, 2):
        books.upsert(book_id=bid, topic=f"书{bid}", genre="g")
        e = entities.get_or_create(book_id=bid, name="主角", kind=EntityKind.CHARACTER)
        injuries.upsert(book_id=bid, entity_id=e.id, name="伤",
                        kind=InjuryKind.WOUND, severity=InjurySeverity.LIGHT,
                        status=InjuryStatus.NEW, chapter_no=1)
    assert len(injuries.list_for_book(1)) == 1
    assert len(injuries.list_for_book(2)) == 1
