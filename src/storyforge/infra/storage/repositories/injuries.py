"""伤势仓储 —— `injuries` 表的读写（**痛点 1「伤势永不恢复」的落地环**）。

## 为什么这一层是伤势恢复的必要条件

`injuries` 表建了（`models.py:432`，含恢复计划三列），
`HealthScheduler` 排节拍的能力也齐了（315 行，阈值表全组合覆盖），
`attach_health_beats` 注入细纲也实现了 —— 但**整条链断在这里**：

    ❌ `load_active_injuries()` 零调用（只有测试）
    ❌ `repositories/` 是空目录 → `injuries` 表**零生产写入路径**
    ❌ `books.py:303` 的 `build_detail_batch` 没传 `health_schedule`
       → 默认 None → `if health_schedule is not None` 恒假
       → `health_beats_injected` 永远 0

★ 关键认识：**只补那个参数是不够的**。没有伤势行，调度器拿到的是空列表，
`health_beats_injected` 依然恒 0 —— 只是从"没传"变成"传了个空"。
本仓储让伤势**真的落库**，那条链才有输入。

## 章号口径（必须全书递增，D-15）

旧项目 P0 事故：`since_ch` 写入侧存**卷内号**、消费侧按**全书号**比对
→ `elapsed = 10012 - 1 = 10011` → 阈值仅 1~3 → **每章恒真**
→ 每个损伤每章都挂「本轮必须推进」→ Editor 每章判「未推进」→ 4~5 分。

本仓储**不做任何章号换算** —— 入参与出参一律是全书递增号，
换算只能发生在调用方显式做（避免"某处偷偷减了卷首偏移"）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from storyforge.domain.enums import (
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
)
from storyforge.domain.health import Injury
from storyforge.infra.storage.models import Injury as InjuryRow
from storyforge.infra.storage.repositories.base import Repository


class InjuryRepository(Repository[Injury]):
    """`injuries` 表读写 —— row ↔ `domain.health.Injury`。

    转换集中在 `to_domain`，与
    `schedulers/health_scheduler.py:load_active_injuries` 的口径**一致**
    （后者是既有实现，本仓储不另立一套）。
    """

    model = InjuryRow

    def to_domain(self, row: Any) -> Injury:
        return Injury(
            id=int(row.id),
            book_id=int(row.book_id),
            entity_id=int(row.entity_id),
            kind=InjuryKind(str(row.kind)),
            name=str(row.name),
            severity=InjurySeverity(str(row.severity)),
            status=InjuryStatus(str(row.status)),
            since_chapter=int(row.since_chapter),
            updated_chapter=int(row.updated_chapter),
            expected_heal_chapter=(
                int(row.expected_heal_chapter)
                if row.expected_heal_chapter is not None else None
            ),
            heal_scene_planned=bool(row.heal_scene_planned),
            heal_condition=row.heal_condition,
            is_permanent=bool(row.is_permanent),
            resolved_chapter=(
                int(row.resolved_chapter)
                if row.resolved_chapter is not None else None
            ),
        )

    # -- 查询 ---------------------------------------------------------------

    def list_for_book(self, book_id: int) -> list[Injury]:
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(InjuryRow)
                    .where(InjuryRow.book_id == book_id)
                    .order_by(InjuryRow.id)
                )
                .scalars()
                .all()
            )
            return [self.to_domain(r) for r in rows]

    def list_active(self, book_id: int) -> list[Injury]:
        """活跃伤势（未痊愈/未解除）—— **`HealthScheduler` 的输入**。

        与 `Injury.is_active` 同口径：排除 `HEALED` / `REMOVED`。
        """
        inactive = (InjuryStatus.HEALED.value, InjuryStatus.REMOVED.value)
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(InjuryRow)
                    .where(
                        InjuryRow.book_id == book_id,
                        InjuryRow.status.notin_(inactive),
                    )
                    .order_by(InjuryRow.id)
                )
                .scalars()
                .all()
            )
            return [self.to_domain(r) for r in rows]

    def find_by_entity(self, book_id: int, entity_id: int) -> list[Injury]:
        with self.session() as sess:
            rows = (
                sess.execute(
                    select(InjuryRow).where(
                        InjuryRow.book_id == book_id,
                        InjuryRow.entity_id == entity_id,
                    )
                )
                .scalars()
                .all()
            )
            return [self.to_domain(r) for r in rows]

    # -- 写入 ---------------------------------------------------------------

    def upsert(
        self,
        *,
        book_id: int,
        entity_id: int,
        name: str,
        kind: InjuryKind | str,
        severity: InjurySeverity | str,
        status: InjuryStatus | str,
        chapter_no: int,
        expected_heal_chapter: int | None = None,
        heal_condition: str | None = None,
        is_permanent: bool = False,
    ) -> Injury:
        """按 `(book_id, entity_id, name)` 插入或更新伤势。

        **识别口径**：同一实体上**同名**的伤势视为同一条（更新），
        否则新建。旧项目把伤势塞在 `CharacterState.state` JSON 里，
        没有主键概念 → 同一条伤每次抽取都可能变成新的。

        `updated_chapter` 恒为 `chapter_no`（本次观察到的章）；
        `since_chapter` 只在**首次创建**时写入，后续更新不动它 ——
        否则"伤了多久"会被每次抽取重置为 0。
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("伤势名不得为空")
        kind_v = kind.value if isinstance(kind, InjuryKind) else str(kind)
        sev_v = severity.value if isinstance(severity, InjurySeverity) else str(severity)
        st_v = status.value if isinstance(status, InjuryStatus) else str(status)

        with self.session() as sess:
            row = (
                sess.execute(
                    select(InjuryRow).where(
                        InjuryRow.book_id == book_id,
                        InjuryRow.entity_id == entity_id,
                        InjuryRow.name == clean,
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = InjuryRow(
                    book_id=book_id,
                    entity_id=entity_id,
                    name=clean,
                    kind=kind_v,
                    severity=sev_v,
                    status=st_v,
                    # ★ 首次创建才写 since_chapter
                    since_chapter=int(chapter_no),
                    updated_chapter=int(chapter_no),
                    expected_heal_chapter=expected_heal_chapter,
                    heal_scene_planned=False,
                    heal_condition=heal_condition,
                    is_permanent=bool(is_permanent),
                    # ★ 若首次登记就已是终态（如"上来就是旧伤已痊愈"），
                    #   也要写 resolved_chapter —— 否则第二次 upsert 会走
                    #   更新分支并把它覆盖成更晚的章号（实测踩到）。
                    resolved_chapter=(
                        int(chapter_no)
                        if st_v in (InjuryStatus.HEALED.value, InjuryStatus.REMOVED.value)
                        else None
                    ),
                )
                sess.add(row)
            else:
                row.kind = kind_v
                row.severity = sev_v
                row.status = st_v
                row.updated_chapter = int(chapter_no)
                if expected_heal_chapter is not None:
                    row.expected_heal_chapter = expected_heal_chapter
                if heal_condition is not None:
                    row.heal_condition = heal_condition
                if is_permanent:
                    row.is_permanent = True
                # 终态写入 resolved_chapter（恢复率的**唯一证据**）
                if st_v in (InjuryStatus.HEALED.value, InjuryStatus.REMOVED.value):
                    if row.resolved_chapter is None:
                        row.resolved_chapter = int(chapter_no)
            sess.flush()
            return self.to_domain(row)

    def mark_heal_planned(
        self, injury_id: int, *, expected_heal_chapter: int
    ) -> None:
        """标记"恢复节拍已排进细纲"（`heal_scene_planned=True`）。

        ★ 这一列是消除旧项目**死锁**的关键（`models.py:455`）：
        只有它为 True 时才允许催促推进，否则 writer 拿到
        「必须推进」却没有场景支撑 → 只能忽略 → Editor 判低分 → 死循环。
        """
        with self.session() as sess:
            row = sess.get(InjuryRow, injury_id)
            if row is None:
                raise KeyError(f"伤势 {injury_id} 不存在")
            row.heal_scene_planned = True
            row.expected_heal_chapter = int(expected_heal_chapter)
