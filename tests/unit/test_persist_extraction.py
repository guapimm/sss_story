"""实体/关系落库链路测试 —— 防的是"抽取跑了但库里一行没有"。

## 本文件防的是什么（**本轮修的真实缺口**）

在修复之前，链路是断的：

    chapter_nodes.py:366  resolve_entities   ✅ 抽了实体
    chapter_nodes.py:374  extract_relations  ✅ 抽了关系
    chapter_nodes.py:383  写 extractions/*.json ✅ 落盘
    → ❌ entities / entity_relations 两张表零写入

`EntityRepository`（含 `get_or_create`）**全仓无生产调用点**，
只被 `repositories/__init__.py` 导出。而 `make_commit_node` 的 docstring
自己写着"DB 入库待 infra/storage/repositories 实现"。

★ 为什么必须写成测试：这是「**表建好了、仓储写好了、没人用**」型缺陷 ——
它**不会报错**。没有测试时，任何人删掉接线代码都不会有红灯，
于是缺口会悄悄回来。故本文件钉住两件事：

  1. `persist_extraction` 本身正确（幂等 / 别名 / 演化 / 不静默降级）
  2. **commit 节点真的调用了它**（端到端 —— 这才是缺口的正身）
"""

from __future__ import annotations

import json

import pytest

from storyforge.domain.enums import EntityKind, RelationKind
from storyforge.infra.storage.repositories import (
    BookRepository,
    EntityRepository,
    RelationRepository,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


@pytest.fixture
def books(db) -> BookRepository:
    return BookRepository(db)


@pytest.fixture
def entities(db) -> EntityRepository:
    return EntityRepository(db)


@pytest.fixture
def relations(db) -> RelationRepository:
    return RelationRepository(db)


@pytest.fixture
def book_id(books) -> int:
    return books.upsert(book_id=7001, topic="测试书", genre="xianxia").id


class _Out:
    """极简替身：模拟 Pydantic 输出对象（只需要属性访问）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Rel:
    def __init__(self, f, t, kind, **kw):
        self.from_entity = f
        self.to_entity = t
        self.kind = kind
        self.intensity = kw.get("intensity", 50)
        self.sentiment = kw.get("sentiment", "neutral")
        self.surface_facing = kw.get("surface_facing", "")
        self.true_facing = kw.get("true_facing", "")
        self.is_mutual = kw.get("is_mutual", True)


def _persist(entities, relations, *, book_id, chapter_no, erepo, rrepo, kinds=None):
    from storyforge.graph.nodes.persist import persist_extraction

    return persist_extraction(
        entities=entities, relations=relations, book_id=book_id,
        chapter_no=chapter_no, entity_repo=erepo, relation_repo=rrepo,
        mention_kinds=kinds,
    )


# --------------------------------------------------------------------------- #
# 一、实体落库
# --------------------------------------------------------------------------- #


def test_entities_are_persisted(entities, relations, book_id) -> None:
    """最基本的一条：抽到的实体**真的进库了**。"""
    out = _Out(mentions=["林默", "苏婉"], links={}, new_canonical={})
    res = _persist(out, None, book_id=book_id, chapter_no=1,
                   erepo=entities, rrepo=relations)

    assert res.entities_created == 2
    assert len(entities.all_names(book_id)) == 2
    assert entities.find_by_name(book_id, "林默") is not None


def test_alias_does_not_create_second_row(entities, relations, book_id) -> None:
    """★ 别名归一 —— 旧项目「同一实体两个名字被建成两行」的直接对策。

    第 2 章模型把 `婉儿` 归到规范名 `苏婉`；若别名不落库，
    下次再提 `婉儿` 时 `find_by_name` 找不到 → 又建一行。
    """
    _persist(_Out(mentions=["苏婉"], links={}, new_canonical={}),
             None, book_id=book_id, chapter_no=1, erepo=entities, rrepo=relations)
    _persist(_Out(mentions=["婉儿"], links={},
                  new_canonical={"婉儿": "苏婉"}),
             None, book_id=book_id, chapter_no=2, erepo=entities, rrepo=relations)

    assert len(entities.all_names(book_id)) == 1, "别名被建成了第二行"
    found = entities.find_by_name(book_id, "婉儿")
    assert found is not None and found.canonical_name == "苏婉"


def test_mention_kind_is_used(entities, relations, book_id) -> None:
    """实体类型来自 scene 抽取，不是一刀切兜底。

    ★ 为什么钉这条：全部落成兜底类型会让 `EntityKind` 的聚合统计失真 ——
    而"势力实体 0 个"正是旧项目最难看的那个数字。
    """
    out = _Out(mentions=["青云宗"], links={}, new_canonical={})
    _persist(out, None, book_id=book_id, chapter_no=1,
             erepo=entities, rrepo=relations, kinds={"青云宗": "势力"})

    got = entities.find_by_name(book_id, "青云宗")
    assert got is not None
    assert got.kind == EntityKind.FACTION, f"实际 {got.kind}"


def test_last_seen_advances_but_never_goes_back(entities, relations, book_id) -> None:
    """`last_seen_chapter` 前进但**不回退** —— `first_seen` 同理。

    ★ 回退会让"该实体是否还活跃"判断错乱，与伤势 `defer()`
    的"不倒退"是同一条纪律（见 `domain/teaching.py` 的同款教训：
    初版测试没钉住方向，两种算法都得同一结果 → 等于没测）。
    """
    out = _Out(mentions=["林默"], links={}, new_canonical={})
    _persist(out, None, book_id=book_id, chapter_no=3,
             erepo=entities, rrepo=relations)
    _persist(out, None, book_id=book_id, chapter_no=9,
             erepo=entities, rrepo=relations)
    # ★ 再回写一个更小的章号 —— 不得倒退
    _persist(out, None, book_id=book_id, chapter_no=5,
             erepo=entities, rrepo=relations)

    rec = entities.find_by_name(book_id, "林默")
    assert rec is not None
    assert rec.first_seen_chapter == 3, "first_seen 被后章改动了"
    assert rec.last_seen_chapter == 9, f"last_seen 被回退到 {rec.last_seen_chapter}"


# --------------------------------------------------------------------------- #
# 二、关系落库
# --------------------------------------------------------------------------- #


def test_relations_are_persisted_with_time_fields(entities, relations, book_id) -> None:
    """关系落库，且**时间字段**正确（旧项目没有 until/updated 维度）。"""
    out = _Out(mentions=["林默", "苏婉"], links={}, new_canonical={})
    rels = _Out(relations=[_Rel("林默", "苏婉", "师徒", intensity=70)])
    res = _persist(out, rels, book_id=book_id, chapter_no=5,
                   erepo=entities, rrepo=relations)

    assert res.relations_written == 1
    rows = relations.list_for_book(book_id)
    assert len(rows) == 1
    assert rows[0].relation_kind == RelationKind.MASTER_APPRENTICE
    assert rows[0].since_chapter == 5
    assert rows[0].updated_chapter == 5
    assert rows[0].intensity == 70


def test_relation_is_upserted_not_duplicated(entities, relations, book_id) -> None:
    """★ 同一对实体同一类型**只一条**，复抽是更新（旧项目会累积新边）。"""
    out = _Out(mentions=["林默", "苏婉"], links={}, new_canonical={})
    _persist(out, _Out(relations=[_Rel("林默", "苏婉", "师徒", intensity=70)]),
             book_id=book_id, chapter_no=1, erepo=entities, rrepo=relations)
    _persist(out, _Out(relations=[_Rel("林默", "苏婉", "师徒", intensity=88)]),
             book_id=book_id, chapter_no=9, erepo=entities, rrepo=relations)

    rows = relations.list_for_book(book_id)
    assert len(rows) == 1, "同一关系被写成两条"
    assert rows[0].intensity == 88, "强度没有被更新"
    assert rows[0].since_chapter == 1, "since_chapter 被后章重置了"
    assert rows[0].updated_chapter == 9


def test_relation_kind_normalized(entities, relations, book_id) -> None:
    """自由文本关系类型被归一（旧项目 191 种标签无法聚合）。

    这条测试防的是把 `RelationKind` 只当注解用 —— 那样
    `仇视/对立/朋友` 会原样落库，聚合又失效。
    """
    out = _Out(mentions=["甲", "乙", "丙", "丁"], links={}, new_canonical={})
    rels = _Out(relations=[
        _Rel("甲", "乙", "仇视"),      # → 仇敌
        _Rel("甲", "丙", "对立"),      # → 敌对
        _Rel("甲", "丁", "朋友"),      # → 盟友
    ])
    _persist(out, rels, book_id=book_id, chapter_no=1,
             erepo=entities, rrepo=relations)

    kinds = {r.relation_kind for r in relations.list_for_book(book_id)}
    assert kinds == {RelationKind.NEMESIS, RelationKind.ENEMY, RelationKind.ALLY}


def test_relation_endpoint_resolves_via_alias(entities, relations, book_id) -> None:
    """★ 关系两端用别名/规范名混写时都要能解析。

    实测踩到：第 2 章 `mentions=["林默","婉儿"]`，
    而关系写 `林默→苏婉`（规范名）—— 只登记提及名时它解析失败，
    关系被丢弃并**误报**"未解析到实体"。
    """
    _persist(_Out(mentions=["林默", "苏婉"], links={}, new_canonical={}),
             None, book_id=book_id, chapter_no=1, erepo=entities, rrepo=relations)
    res = _persist(
        _Out(mentions=["婉儿"], links={}, new_canonical={"婉儿": "苏婉"}),
        _Out(relations=[_Rel("林默", "苏婉", "师徒")]),
        book_id=book_id, chapter_no=2, erepo=entities, rrepo=relations,
    )
    assert res.relations_written == 1, f"关系被丢：{res.problems}"
    assert len(relations.list_for_book(book_id)) == 1


def test_relation_endpoint_not_in_mentions_still_persists(
    entities, relations, book_id,
) -> None:
    """两端有名字但不在 `mentions` 里 —— 仍要建（关系证明了实体存在）。

    ★ 若丢弃，关系网会缺角，而"关系单一"正是要治的痛点。
    """
    _persist(_Out(mentions=["林默"], links={}, new_canonical={}), None,
             book_id=book_id, chapter_no=1, erepo=entities, rrepo=relations)
    res = _persist(
        None, _Out(relations=[_Rel("林默", "陌生客", "对手")]),
        book_id=book_id, chapter_no=2, erepo=entities, rrepo=relations,
    )
    assert res.relations_written == 1
    assert entities.find_by_name(book_id, "陌生客") is not None


# --------------------------------------------------------------------------- #
# 三、不得静默降级 / 不得跨书串线
# --------------------------------------------------------------------------- #


def test_unregistered_relation_kind_is_reported(entities, relations, book_id) -> None:
    """未登记的关系类型 → 落 CUSTOM **且留痕**（非静默降级）。

    ★ 与旧项目的区别正在"留痕"：旧项目判不在枚举内就降级为「其他」，
    没有任何人发现 —— 那是难点 4 无法闭合的原因。
    """
    out = _Out(mentions=["甲", "乙"], links={}, new_canonical={})
    rels = _Out(relations=[_Rel("甲", "乙", "莫名其妙的类型")])
    res = _persist(out, rels, book_id=book_id, chapter_no=1,
                   erepo=entities, rrepo=relations)

    assert any("未登记" in p for p in res.problems), res.problems
    rows = relations.list_for_book(book_id)
    assert rows[0].relation_kind == RelationKind.CUSTOM


def test_cross_book_entity_id_is_rejected(entities, relations, books, book_id) -> None:
    """★ `links` 里是 LLM 给的 ID —— 不得假定它属于本书（静默跨书串线）。

    若只按主键取，模型给错 ID 时会把另一本书的实体接进本章关系。
    """
    other_id = books.upsert(book_id=7002, topic="另一本", genre="xianxia").id
    other_ent = entities.get_or_create(book_id=other_id, name="外书角色")

    res = _persist(
        _Out(mentions=["外书角色"], links={"外书角色": other_ent.id},
             new_canonical={}),
        None, book_id=book_id, chapter_no=1, erepo=entities, rrepo=relations,
    )
    assert any("不属于本书" in p for p in res.problems), res.problems


def test_self_loop_relation_is_skipped(entities, relations, book_id) -> None:
    """自环不是社会关系（旧项目"动作被当关系"的残留形态）。"""
    out = _Out(mentions=["甲"], links={}, new_canonical={})
    rels = _Out(relations=[_Rel("甲", "甲", "师徒")])
    res = _persist(out, rels, book_id=book_id, chapter_no=1,
                   erepo=entities, rrepo=relations)

    assert res.relations_written == 0
    assert any("同一实体" in p for p in res.problems), res.problems


def test_none_inputs_are_not_an_error(entities, relations, book_id) -> None:
    """该章什么都没抽到 → 不是错误（不能因此判提交失败）。"""
    res = _persist(None, None, book_id=book_id, chapter_no=1,
                   erepo=entities, rrepo=relations)
    assert res.problems == []
    assert res.relations_written == 0


# --------------------------------------------------------------------------- #
# 四、★ 端到端：commit 节点必须真的落库（本缺口的正身）
# --------------------------------------------------------------------------- #


async def test_commit_node_persists_to_db(db, tmp_repo, book_id) -> None:
    """★★ 这条是缺口本身 —— commit 节点必须把抽取产物写进库。

    只测 `persist_extraction` 是不够的：那个函数写得再对，
    只要 commit 节点**没调它**，缺口依然在（这正是修复前的状态）。
    故本测试跑**真实 commit 节点**，断言库里出现了数据。
    """
    import json as _json

    from storyforge.infra.storage.repositories import (
        EntityRepository,
        RelationRepository,
    )
    from storyforge.graph.graphs.chapter_nodes import (
        ChapterDeps,
        make_commit_node,
    )

    erepo = EntityRepository(db)
    rrepo = RelationRepository(db)

    # 造一个最小 ArtifactStore：只放"正文"与"抽取产物"两个文件
    from storyforge.infra.storage.artifacts import ArtifactStore

    store = ArtifactStore(tmp_repo)
    store.write_text(ArtifactStore.prose_key(book_id, 1), "林默拜入青云宗。")
    ref = ArtifactStore.extraction_key(book_id, 1)
    store.write_json(ref, {
        "scene": None,
        "entities": {
            "mentions": ["林默", "青云宗"],
            "links": {},
            "new_canonical": {},
        },
        "relations": {
            "relations": [{
                "from_entity": "林默", "to_entity": "青云宗",
                "kind": "归属", "intensity": 80, "sentiment": "positive",
                "surface_facing": "", "true_facing": "", "is_mutual": True,
            }],
        },
        "mention_kinds": {"林默": "人物", "青云宗": "势力"},
        "problems": [],
    })

    deps = ChapterDeps(client=None, store=store,
                       entities=erepo, relations=rrepo)
    node = make_commit_node(deps)
    out = await node({"book_id": book_id, "global_no": 1,
                      "extraction_ref": ref.key})

    assert out["committed"] is True
    # ★ 核心断言：库里**真的有数据**
    assert len(erepo.all_names(book_id)) == 2, (
        "commit 节点没有把实体写进库 —— 抽取链路又断了"
    )
    assert rrepo.count_for_book(book_id) == 1, (
        "commit 节点没有把关系写进库"
    )
    assert out.get("persisted") is True
    assert "已入库" in out.get("commit_note", "")


async def test_commit_node_without_repos_says_so(tmp_repo, book_id) -> None:
    """没注入仓储时**如实说明未入库**，不假装成功（「缺省即不通过」）。"""
    from storyforge.infra.storage.artifacts import ArtifactStore
    from storyforge.graph.graphs.chapter_nodes import (
        ChapterDeps,
        make_commit_node,
    )

    store = ArtifactStore(tmp_repo)
    store.write_text(ArtifactStore.prose_key(book_id, 1), "正文。")
    ref = ArtifactStore.extraction_key(book_id, 1)
    store.write_json(ref, {"entities": None, "relations": None})

    deps = ChapterDeps(client=None, store=store)      # 无仓储
    node = make_commit_node(deps)
    out = await node({"book_id": book_id, "global_no": 1,
                      "extraction_ref": ref.key})

    assert "未注入仓储" in out.get("commit_note", "")
    assert out.get("persisted") is None      # 不得谎报已入库
