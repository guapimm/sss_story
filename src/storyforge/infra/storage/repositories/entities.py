"""实体仓储 —— `entities` 表的读写。

## 为什么伤势接线必须先有它

`injuries.entity_id` 是 `ForeignKey("entities.id", ondelete="CASCADE")`
（`models.py:468`），且 SQLite 的外键是**强制开启**的
（`db.py:42` 的 `PRAGMA foreign_keys=ON`）。

因此想让一条 `Injury` 落库，**必须先有一条 `Entity`** ——
`HealthScheduler` 需要的是"某角色的某处伤"。
这就是"只补一个 `health_schedule=` 参数"不够用的原因：
没有实体、没有伤势行，调度器拿到的永远是空列表。

## 去重口径

`entities` 有唯一约束 `(book_id, kind, canonical_name)`
（`models.py:245`）。本仓储提供 `get_or_create`，
把"按名字找实体、找不到就建"这个动作**收敛到一处** ——
旧项目的实体归一散落在多模块，是三套类型词汇表冲突的温床。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from storyforge.domain.enums import EntityKind, LifecycleState, normalize_entity_kind
from storyforge.infra.storage.models import Entity as EntityRow
from storyforge.infra.storage.repositories.base import Repository


class EntityRecord:
    """实体（领域侧视图）。

    ⚠️ 刻意**不用** `domain/entities.py` 的富模型：本层只做存取，
    领域逻辑（生命周期转移、别名归一）在 `domain/`，
    本层不得反向依赖（见 `base.py` 的分层约束）。
    """

    __slots__ = ("id", "book_id", "kind", "canonical_name", "aliases",
                 "first_seen_chapter", "last_seen_chapter", "lifecycle", "is_active")

    def __init__(
        self,
        *,
        id: int,
        book_id: int,
        kind: EntityKind,
        canonical_name: str,
        aliases: list[str],
        first_seen_chapter: int,
        last_seen_chapter: int | None,
        lifecycle: str,
        is_active: bool,
    ) -> None:
        self.id = id
        self.book_id = book_id
        self.kind = kind
        self.canonical_name = canonical_name
        self.aliases = aliases
        self.first_seen_chapter = first_seen_chapter
        self.last_seen_chapter = last_seen_chapter
        self.lifecycle = lifecycle
        self.is_active = is_active

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"EntityRecord(id={self.id}, kind={self.kind.value}, "
                f"name={self.canonical_name!r})")


class EntityRepository(Repository[EntityRecord]):
    """`entities` 表读写。"""

    model = EntityRow

    def to_domain(self, row: Any) -> EntityRecord:
        import json

        try:
            aliases = json.loads(row.aliases or "[]")
        except (ValueError, TypeError):
            # ★ 坏 JSON 不得让整本书读不出来。旧项目在这里会直接抛。
            aliases = []
        return EntityRecord(
            id=int(row.id),
            book_id=int(row.book_id),
            kind=normalize_entity_kind(str(row.kind)),
            canonical_name=str(row.canonical_name),
            aliases=[str(a) for a in aliases],
            first_seen_chapter=int(row.first_seen_chapter),
            last_seen_chapter=(
                int(row.last_seen_chapter)
                if row.last_seen_chapter is not None else None
            ),
            lifecycle=str(row.lifecycle),
            is_active=bool(row.is_active),
        )

    # -- 查询 ---------------------------------------------------------------

    def find_by_name(
        self, book_id: int, name: str, *, kind: EntityKind | None = None
    ) -> EntityRecord | None:
        """按规范名**或别名**查找（别名是旧项目反复出错的地方）。

        `name` 会先做 `normalize_entity_kind` 之外的**纯名字**匹配；
        别名命中同样返回 —— 否则"同一实体两个名字"会被建成两行。
        """
        target = (name or "").strip()
        if not target:
            return None
        with self.session() as sess:
            stmt = select(EntityRow).where(EntityRow.book_id == book_id)
            if kind is not None:
                stmt = stmt.where(EntityRow.kind == kind.value)
            for row in sess.execute(stmt).scalars().all():
                if str(row.canonical_name) == target:
                    return self.to_domain(row)
            # 别名匹配（canonical_name 没中才查，避免多一次 JSON 解析）
            for row in sess.execute(stmt).scalars().all():
                rec = self.to_domain(row)
                if target in rec.aliases:
                    return rec
        return None

    def find_by_kind(self, book_id: int, kind: EntityKind) -> list[EntityRecord]:
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(EntityRow).where(
                        EntityRow.book_id == book_id,
                        EntityRow.kind == kind.value,
                    )
                )
                .scalars()
                .all()
            )
            return [self.to_domain(r) for r in rows]

    # -- 写入 ---------------------------------------------------------------

    def get_or_create(
        self,
        *,
        book_id: int,
        name: str,
        kind: str | EntityKind = EntityKind.CHARACTER,
        chapter_no: int = 1,
    ) -> EntityRecord:
        """按 `(book_id, kind, canonical_name)` 取或建（幂等）。

        唯一约束是 `(book_id, kind, canonical_name)`，
        所以**同名不同 kind 是两条** —— 与 `models.py:245` 一致。
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("实体名不得为空")
        ek = kind if isinstance(kind, EntityKind) else normalize_entity_kind(str(kind))

        existing = self.find_by_name(book_id, clean, kind=ek)
        if existing is not None:
            return existing

        with self.session() as sess:
            row = EntityRow(
                book_id=book_id,
                kind=ek.value,
                canonical_name=clean,
                aliases="[]",
                first_seen_chapter=int(chapter_no),
                lifecycle=LifecycleState.ACTIVE.value,
                is_active=True,
            )
            sess.add(row)
            sess.flush()          # 拿到自增 id
            return self.to_domain(row)
