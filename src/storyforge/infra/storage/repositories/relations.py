"""关系仓储 —— `entity_relations` 表的读写（痛点 4 的落地环）。

## 为什么需要它（旧项目的病）

旧项目 `relationships` 一张表混装三种语义，实测 966 行里：

  · **人物-人物仅 254/966 = 26.3%** —— 其余 73.7% 是动作流水账
    （查看 / 拾取 / 值守…），那些**不属于关系**。
  · `relation_type` 是**自由文本**：254 条人物边用了 **191 种**标签，
    自带 9 词受控词表只覆盖 **31/254 = 12.2%**
    → 系统无法聚合/比较，`敌对/对手/仇视/对立` 无法归一。
  · **两端是名字字符串**（`char_a` / `char_b`），没有 ID、没有外键
    → 改名即断链；392 个裸字符串就是"实体"的全部身份。
  · **没有 `until_chapter`** → 关系结束只靠 `status` 字符串，
    实测「五元组匹配」把 `type_b` 当匹配键 → LLM 这次写"组织"下次写"势力"
    → 匹配失败 → **旧边不解除、新边继续累积**。

本仓储的对策：

  · 边**两端是外键** `from_entity_id` / `to_entity_id`（改名不断链）
  · `relation_kind` 是**枚举**（可归一、可聚合）
  · `status` 有明确枚举 + `updated_chapter` 标记演化时刻
  · 唯一约束 `(book_id, from, to, relation_kind)` → **同一对实体同一类型只有一条**
    （重复抽取是**更新**而非新增）

## 分层

本层只做存取与 row↔domain 转换；`domain/entities.py` 的领域逻辑不动，
也**不得反向依赖**本层（见 `base.py`）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from storyforge.domain.entities import EntityRelation
from storyforge.domain.enums import (
    RelationKind,
    RelationSentiment,
    RelationStatus,
    normalize_relation_kind,
)
from storyforge.infra.storage.models import EntityRelation as RelationRow
from storyforge.infra.storage.repositories.base import Repository

#: 归一失败时的兜底（**不得**静默丢弃：丢弃会让"关系单一"复发）
FALLBACK_KIND = RelationKind.CUSTOM


class RelationRepository(Repository[EntityRelation]):
    """`entity_relations` 表读写。"""

    model = RelationRow

    def to_domain(self, row: Any) -> EntityRelation:
        return EntityRelation(
            id=int(row.id),
            book_id=int(row.book_id),
            from_entity_id=int(row.from_entity_id),
            to_entity_id=int(row.to_entity_id),
            relation_kind=normalize_relation_kind(str(row.relation_kind)),
            since_chapter=int(row.since_chapter),
            updated_chapter=int(row.updated_chapter),
            intensity=int(row.intensity),
            sentiment=_sentiment(str(row.sentiment)),
            surface_facing=row.surface_facing,
            true_facing=row.true_facing,
            is_mutual=bool(row.is_mutual),
            status=_status(str(row.status)),
            changed_by_event_id=(
                int(row.changed_by_event_id)
                if row.changed_by_event_id is not None else None
            ),
        )

    # -- 查询 ---------------------------------------------------------------

    def list_for_book(self, book_id: int) -> list[EntityRelation]:
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(RelationRow)
                    .where(RelationRow.book_id == book_id)
                    .order_by(RelationRow.id)
                )
                .scalars()
                .all()
            )
            return [self.to_domain(r) for r in rows]

    def list_for_entity(self, book_id: int, entity_id: int) -> list[EntityRelation]:
        """某实体参与的所有关系（**双向**）—— `find_by_name` 的图侧对应。

        ★ 必须同时查 from / to：只查一侧会让"别人指向我"的关系消失，
        这正是旧项目"关系网看起来是扁平的"的一个成因。
        """
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(RelationRow).where(
                        RelationRow.book_id == book_id,
                        (RelationRow.from_entity_id == entity_id)
                        | (RelationRow.to_entity_id == entity_id),
                    )
                )
                .scalars()
                .all()
            )
            return [self.to_domain(r) for r in rows]

    def count_for_book(self, book_id: int) -> int:
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(RelationRow.id).where(RelationRow.book_id == book_id)
                )
                .scalars()
                .all()
            )
            return len(rows)

    # -- 写入 ---------------------------------------------------------------

    def upsert(
        self,
        *,
        book_id: int,
        from_entity_id: int,
        to_entity_id: int,
        kind: str | RelationKind = RelationKind.CUSTOM,
        chapter_no: int,
        intensity: int = 50,
        sentiment: str | RelationSentiment = RelationSentiment.NEUTRAL,
        surface_facing: str | None = None,
        true_facing: str | None = None,
        is_mutual: bool = True,
    ) -> EntityRelation:
        """按 `(book_id, from, to, kind)` **取或建或更新**（幂等）。

        ★ 为什么是 upsert 而不是 insert：
        抽取是**每章都跑**的，同一对师徒关系会在几十章里被反复抽到。
        若每次 insert，唯一约束会炸；若跳过已存在，则强度/暗线**永不更新**
        （旧项目正是"新边继续累积、旧边不解除"的镜像错误）。

        本实现取中间：**存在则更新演化字段**（intensity / sentiment /
        明暗 / `updated_chapter`），`since_chapter` **保持首次值**
        （关系的"开始时间"不该被后续章节覆盖 —— 那是旧项目
        `since_ch` 口径混乱的同款陷阱）。
        """
        ek = kind if isinstance(kind, RelationKind) else normalize_relation_kind(str(kind))
        es = (sentiment if isinstance(sentiment, RelationSentiment)
              else _sentiment(str(sentiment)))

        with self.session() as sess:
            row = (
                sess.execute(
                    select(RelationRow).where(
                        RelationRow.book_id == book_id,
                        RelationRow.from_entity_id == from_entity_id,
                        RelationRow.to_entity_id == to_entity_id,
                        RelationRow.relation_kind == ek.value,
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = RelationRow(
                    book_id=book_id,
                    from_entity_id=from_entity_id,
                    to_entity_id=to_entity_id,
                    relation_kind=ek.value,
                    intensity=int(intensity),
                    sentiment=es.value,
                    surface_facing=surface_facing or None,
                    true_facing=true_facing or None,
                    is_mutual=bool(is_mutual),
                    since_chapter=int(chapter_no),
                    status=RelationStatus.ACTIVE.value,
                    updated_chapter=int(chapter_no),
                )
                sess.add(row)
            else:
                # ★ 演化：只更新会变的字段，`since_chapter` 保留首次值
                row.intensity = int(intensity)
                row.sentiment = es.value
                if surface_facing:
                    row.surface_facing = surface_facing
                if true_facing:
                    row.true_facing = true_facing
                row.is_mutual = bool(is_mutual)
                row.updated_chapter = int(chapter_no)
                row.status = RelationStatus.ACTIVE.value
            sess.flush()
            return self.to_domain(row)

    def resolve_kind(self, raw: str) -> RelationKind:
        """把 LLM 给的自由文本关系类型归一到枚举。

        ★ `normalize_relation_kind` **未知类型抛错**（这是消除"自由文本"的
        直接手段）。但抽取链路的容错要求"不因一个坏标签中断整章"——
        故本方法在**仓储层**捕获并落到 `OTHER`，同时由调用方记录告警，
        而不是在这里静默吞掉（静默会让"关系单一"复发而不留痕）。
        """
        try:
            return normalize_relation_kind(raw)
        except (ValueError, KeyError):
            return FALLBACK_KIND


def _sentiment(raw: str) -> RelationSentiment:
    try:
        return RelationSentiment(raw)
    except ValueError:
        return RelationSentiment.NEUTRAL


def _status(raw: str) -> RelationStatus:
    try:
        return RelationStatus(raw)
    except ValueError:
        return RelationStatus.ACTIVE


__all__ = ["FALLBACK_KIND", "RelationRepository"]
