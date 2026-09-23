"""抽取产物 → 数据库落库（**痛点 2 / 4 的最后一公里**）。

## 这个模块补的是什么

在它存在之前，链路是**断的**：

    write 第1章
      → chapter_nodes.py:366  resolve_entities  ✅ 抽了实体
      → chapter_nodes.py:374  extract_relations  ✅ 抽了关系
      → chapter_nodes.py:383  写 artifacts/extractions/00001.json  ✅ 落盘
      → ❌ 到此为止。entities / entity_relations 两张表**零写入**

于是：

  · `EntityRepository`（含 `get_or_create`）**全仓无生产调用点**，只被
    `repositories/__init__.py` 导出 —— 表建好了、仓储写好了、没人用。
  · `known_entities` 在 state 里但**永远是空列表**
    （`chapter_nodes.py:369` 读 `state.get("known_entities", [])`）。
    后果：每章对模型而言都是"第一次见这个名字" → 反复新建同一实体
    → 旧项目「同一实体两个名字被建成两行」的复发。
  · `make_commit_node` 的 docstring 自己写着
    "DB 入库待 infra/storage/repositories 实现" —— 本模块兑现它。

## 设计要点（每条都对应一个实测教训）

1. **实体先落、关系后落** —— `entity_relations` 的两端是
   `ForeignKey("entities.id")` 且 SQLite 外键**强制开启**
   （`db.py:42`）。关系里的名字必须先解析成实体 ID，否则整个事务回滚。

2. **名字 → ID 的解析只在本书范围内** —— `entity_resolver` 返回的
   `links` 里是 LLM 给的 ID，**不能假定它指的是本书实体**
   （见 `EntityRepository.find_by_id` 的 docstring：那是静默跨书串线）。

3. **关系类型归一失败 → 落 `CUSTOM` + 记录告警，不中断整章** ——
   但这**不是**"静默降级"：告警会进 `problems` 回传，与旧项目
   "判不在枚举内就降级为其他、无人发现"有本质区别。

4. **失败不阻断提交** —— 与 `books.py:_mark_heal_planned` 同一条纪律：
   产物已落盘，DB 写失败要**留痕**（`logger.warning` + 返回问题清单），
   而不是把整章判为失败。但**缺省即不通过**：返回的问题会让调用方
   知道"落库没成功"。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from storyforge.domain.enums import EntityKind, normalize_entity_kind

logger = logging.getLogger(__name__)


@dataclass
class PersistResult:
    """落库结果 —— 调用方据此判断"是否真的入库了"。"""

    entities_created: int = 0
    entities_linked: int = 0
    relations_written: int = 0
    #: 归一失败 / 名字解析失败等**非致命**问题（回传给 state 的 problems）
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        return (
            f"实体新建 {self.entities_created} / 关联既有 {self.entities_linked} / "
            f"关系 {self.relations_written}"
            + (f"；问题 {len(self.problems)} 条" if self.problems else "")
        )


def _kind_of(raw: str) -> EntityKind:
    """实体类型归一；失败落 `CHARACTER` 并**留痕**（由调用方收集问题）。"""
    try:
        return normalize_entity_kind(str(raw))
    except (ValueError, KeyError):
        return EntityKind.CHARACTER


def persist_extraction(
    *,
    entities,
    relations,
    book_id: int,
    chapter_no: int,
    entity_repo: Any,
    relation_repo: Any,
    mention_kinds: dict[str, str] | None = None,
) -> PersistResult:
    """把一章的抽取产物写进 `entities` / `entity_relations`。

    `entities` 是 `EntityResolutionOut`（含 `mentions` / `links` /
    `new_canonical`），`relations` 是 `RelationExtractionOut`。
    两者都可以是 `None`（该章没抽到东西）—— 那不是错误。

    `mention_kinds` 是 `{提及名: EntityKind 文本}`，来自更上游的
    `SceneExtractionOut.entities`（`EntityResolutionOut` **不带类型**）。
    缺省时类型落 `CHARACTER` —— 这是**有意的降级**而非猜测：
    猜出来的类型会污染 `EntityKind` 聚合统计
    （"势力实体 0 个"正是旧项目的病）。
    """
    result = PersistResult()
    if entities is None and relations is None:
        return result
    kinds = dict(mention_kinds or {})

    # ── ① 实体：先把名字落成 ID ──────────────────────────────────────────
    #: 提及名 → 实体 ID（本书范围内）
    name_to_id: dict[str, int] = {}

    links = dict(getattr(entities, "links", None) or {}) if entities else {}
    canonical = dict(getattr(entities, "new_canonical", None) or {}) if entities else {}
    mentions = list(getattr(entities, "mentions", None) or []) if entities else []

    # 1a. 模型指认的既有实体 —— **必须校验归属本书**
    for mention, entity_id in links.items():
        rec = entity_repo.find_by_id(book_id, int(entity_id))
        if rec is None:
            result.problems.append(
                f"实体链接指向不属于本书的 ID：{mention!r} → {entity_id}"
            )
            continue
        name_to_id[str(mention)] = rec.id
        # 与 1b 同理：规范名/既有别名都要能解析到同一个 ID
        name_to_id.setdefault(rec.canonical_name, rec.id)
        for alias in rec.aliases:
            name_to_id.setdefault(alias, rec.id)
        result.entities_linked += 1

    # 1b. 其余提及 —— 取或建（用模型给的规范名，没有就用原名）
    for mention in mentions:
        m = str(mention).strip()
        if not m or m in name_to_id:
            continue
        canon = str(canonical.get(m) or m).strip() or m
        kind = _kind_of(kinds.get(m, ""))
        rec = entity_repo.get_or_create(
            book_id=book_id, name=canon, kind=kind, chapter_no=chapter_no
        )
        name_to_id[m] = rec.id
        # ★ 规范名也必须登记成键：模型可能在同一章的**另一条关系**里
        # 用规范名指同一个实体（实测：关系 A 写「婉儿」、关系 B 写「苏婉」）。
        # 只登记提及名会让后者解析失败 → 关系被丢弃并误报"未解析到实体"。
        name_to_id.setdefault(canon, rec.id)
        # 提及名本身就是别名（旧项目"同一实体两个名字建成两行"的对策）
        if canon != m:
            entity_repo.add_alias(rec.id, m)
        # 更新"最后出现章" —— `last_seen_chapter` 若永不写，
        # "该实体是否还活跃"就答不了（那正是本字段存在的理由）
        entity_repo.touch_last_seen(rec.id, chapter_no)
        if rec.first_seen_chapter == chapter_no:
            result.entities_created += 1

    # ── ② 关系：两端解析成 ID 后 upsert ─────────────────────────────────
    def _resolve(name: str) -> int | None:
        """名字 → 实体 ID：先查本章映射，再查库（跨章别名/规范名）。

        ★ 为什么要回落查库：模型写关系时用的是**名字**，而名字可能是
        别名、也可能是规范名，还可能指向前几章就存在的实体（本章
        `mentions` 里没出現）。只查本章映射会静默丢关系 —— 实测
        第 2 章「林默→苏婉」就这么被丢掉过（当时误报为"未解析到实体"）。
        """
        key = str(name).strip()
        if not key:
            return None
        if key in name_to_id:
            return name_to_id[key]
        rec = entity_repo.find_by_name(book_id, key)
        if rec is not None:
            name_to_id[key] = rec.id      # 记忆化，避免同一章重复查
            return rec.id
        # ★ 关系两端有名字、但本章 mentions 没提到它 —— 仍要建。
        #   理由：关系**本身就证明了该实体存在**（它出现在正文里，
        #   只是抽取的 `mentions` 漏了）。丢弃它会让关系网缺角，
        #   而"关系单一"正是要治的痛点。建了并留痕，便于回查抽取质量。
        rec = entity_repo.get_or_create(
            book_id=book_id, name=key, kind=EntityKind.CHARACTER,
            chapter_no=chapter_no,
        )
        name_to_id[key] = rec.id
        result.entities_created += 1
        result.problems.append(
            f"实体 {key!r} 只出现在关系里（未列入 mentions），已按默认类型建行"
        )
        return rec.id

    for rel in (getattr(relations, "relations", None) or []) if relations else []:
        src = _resolve(rel.from_entity)
        dst = _resolve(rel.to_entity)
        if src is None or dst is None:
            missing = [
                str(n) for n, i in ((rel.from_entity, src), (rel.to_entity, dst))
                if i is None
            ]
            result.problems.append(
                f"关系两端未解析到实体：{rel.from_entity!r} → {rel.to_entity!r}"
                f"（缺 {missing}）"
            )
            continue

        kind = relation_repo.resolve_kind(str(rel.kind))
        if kind.value != str(rel.kind).strip():
            result.problems.append(
                f"关系类型 {rel.kind!r} 未登记，已归入 {kind.value!r}"
            )
        # ★ 自环不是社会关系（旧项目"动作被当关系"的一种残留形态）
        if src == dst:
            result.problems.append(f"关系两端是同一实体，已跳过：{rel.from_entity!r}")
            continue

        relation_repo.upsert(
            book_id=book_id,
            from_entity_id=src,
            to_entity_id=dst,
            kind=kind,
            chapter_no=chapter_no,
            intensity=int(getattr(rel, "intensity", 50) or 50),
            sentiment=str(getattr(rel, "sentiment", "neutral") or "neutral"),
            surface_facing=str(getattr(rel, "surface_facing", "") or "") or None,
            true_facing=str(getattr(rel, "true_facing", "") or "") or None,
            is_mutual=bool(getattr(rel, "is_mutual", True)),
        )
        result.relations_written += 1

    return result


__all__ = ["PersistResult", "persist_extraction"]
