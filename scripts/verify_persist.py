"""诊断：持久化层能否真的把抽取产物写进库（真 DB，独立临时目录）。

## 为什么先写这个再接线

接线前必须证明**这一层本身**是对的，否则接上去后出的问题
分不清是"持久化错"还是"接线错"（本项目记录过同类教训：
`verify_doctor_parity.py` 就是为了分离"拆分"与"行为改变"两种可能）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _Res:
    """极简替身：模拟 `EntityResolutionOut` / `RelationExtractionOut`。"""

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


def main() -> int:
    from storyforge.infra.storage.db import Database
    from storyforge.infra.storage.repositories import (
        BookRepository,
        EntityRepository,
        RelationRepository,
    )
    from storyforge.domain.enums import EntityKind
    from storyforge.graph.nodes.persist import persist_extraction

    tmp = Path(tempfile.mkdtemp(prefix="sf_persist_"))
    db = Database.open(tmp / "t.db")
    db.create_all()

    books = BookRepository(db)
    ents = EntityRepository(db)
    rels = RelationRepository(db)

    book = books.upsert(book_id=9001, topic="测试书", genre="xianxia", mode="story")
    book_id = book.id
    print(f"建书 id={book_id}")

    # ── 第 1 章：三个新实体 + 两条关系 ──────────────────────────────────
    e1 = _Res(
        mentions=["林默", "苏婉", "青云宗"],
        links={},
        new_canonical={"林默": "林默", "苏婉": "苏婉", "青云宗": "青云宗"},
    )
    r1 = _Res(relations=[
        _Rel("林默", "苏婉", "师徒", intensity=70),
        _Rel("林默", "青云宗", "归属", intensity=90),
    ])
    res1 = persist_extraction(
        entities=e1, relations=r1, book_id=book_id, chapter_no=1,
        entity_repo=ents, relation_repo=rels,
    )
    print(f"第1章 → {res1.summary()}")

    # ── 第 2 章：别名 + 关系演化（同一对师徒，强度变化） ────────────────
    e2 = _Res(
        mentions=["林默", "婉儿"],
        links={},
        new_canonical={"林默": "林默", "婉儿": "苏婉"},   # ★ 婉儿 是 苏婉 的别名
    )
    r2 = _Res(relations=[
        _Rel("林默", "婉儿", "师徒", intensity=85),
        _Rel("林默", "苏婉", "对立"),                       # 新关系类型
    ])
    res2 = persist_extraction(
        entities=e2, relations=r2, book_id=book_id, chapter_no=2,
        entity_repo=ents, relation_repo=rels,
    )
    print(f"第2章 → {res2.summary()}")

    print()
    print("=" * 70)
    print("判定")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    all_ents = ents.find_by_kind(book_id, EntityKind.CHARACTER)
    n_ent = len(ents.all_names(book_id))
    checks.append(("实体数 = 3（别名不得建新行）", n_ent == 3, f"实际 {n_ent}"))

    # 别名生效
    found = ents.find_by_name(book_id, "婉儿")
    checks.append(("按别名 '婉儿' 能查到实体", found is not None,
                   f"实际 {found.canonical_name if found else None}"))

    # 关系数：林默-苏婉(师徒) / 林默-青云宗(归属) / 林默-苏婉(敌对) = 3
    n_rel = rels.count_for_book(book_id)
    checks.append(("关系数 = 3（同对同类型只一条，含演化更新）", n_rel == 3,
                   f"实际 {n_rel}"))

    # 演化：师徒强度 70 → 85
    rel_list = rels.list_for_book(book_id)
    master = [r for r in rel_list if r.relation_kind.value == "师徒"]
    intensity_ok = len(master) == 1 and master[0].intensity == 85
    checks.append(("师徒关系强度被更新 70→85 且不新增行", intensity_ok,
                   f"实际 {[(r.relation_kind.value, r.intensity) for r in master]}"))
    since_ok = len(master) == 1 and master[0].since_chapter == 1
    checks.append(("since_chapter 保持首次值 1（未被第2章覆盖）", since_ok,
                   f"实际 {master[0].since_chapter if master else None}"))

    # 双向查询
    linmo = ents.find_by_name(book_id, "林默")
    both = rels.list_for_entity(book_id, linmo.id) if linmo else []
    checks.append(("双向查询：林默 参与的关系 = 3", len(both) == 3, f"实际 {len(both)}"))

    # 跨书 ID 防护
    other = books.upsert(book_id=9002, topic="另一本", genre="xianxia", mode="story")
    bad = _Res(mentions=["张三"], links={"张三": linmo.id if linmo else 0},
               new_canonical={})
    res3 = persist_extraction(
        entities=bad, relations=None, book_id=other.id, chapter_no=1,
        entity_repo=ents, relation_repo=rels,
    )
    cross_ok = any("不属于本书" in p for p in res3.problems)
    checks.append(("跨书 ID 被拦下并留痕", cross_ok, f"problems={res3.problems}"))

    # 未登记关系类型 → CUSTOM + 留痕
    e4 = _Res(mentions=["甲", "乙"], links={}, new_canonical={})
    r4 = _Res(relations=[_Rel("甲", "乙", "莫名其妙的类型")])
    res4 = persist_extraction(
        entities=e4, relations=r4, book_id=book_id, chapter_no=3,
        entity_repo=ents, relation_repo=rels,
    )
    warn_ok = any("未登记" in p for p in res4.problems)
    checks.append(("未登记关系类型 → 留痕（非静默）", warn_ok,
                   f"problems={res4.problems}"))

    # 自环跳过
    r5 = _Res(relations=[_Rel("甲", "甲", "师徒")])
    res5 = persist_extraction(
        entities=None, relations=r5, book_id=book_id, chapter_no=4,
        entity_repo=ents, relation_repo=rels,
    )
    # 注意：entities=None 时 name_to_id 为空 → 应该报"未解析到实体"
    checks.append(("无实体映射时关系不落库并留痕",
                   len(res5.problems) > 0 and res5.relations_written == 0,
                   f"written={res5.relations_written} problems={res5.problems}"))

    passed = 0
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '★'} {name}")
        if not ok:
            print(f"      {detail}")
        passed += ok
    print()
    print(f"  → {passed}/{len(checks)} 通过")
    print("=" * 70)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
