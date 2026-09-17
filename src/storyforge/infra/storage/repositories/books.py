"""书籍仓储 —— `books` 表读写。

## 为什么需要它（`books` 曾是一张死表）

实测：`Book.mode`（`models.py:83`）**从未被写、也从未被读**，
且**全仓没有任何代码创建 `Book` 行** → `books` 表是死表。

后果是两重的：
  1. `injuries` / `entities` / `chapters` 全部 FK 到 `books.id`
     → 没有 Book 行，**任何子表都插不进去**（外键强制开启）
  2. `Book.mode` 是 story / learn 的**唯一持久化归属**，
     但 `config.py:275` 的 `mode` 只写不读 → 模式实际无归属

本仓储把"建一行 Book"变成**一次显式调用**。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from storyforge.domain.enums import BookMode, BookStatus
from storyforge.infra.storage.models import Book as BookRow
from storyforge.infra.storage.repositories.base import Repository


class BookRecord:
    """书籍（领域侧视图）。"""

    __slots__ = ("id", "title", "topic", "mode", "genre", "scale",
                 "target_chapters", "total_volumes", "status",
                 "current_volume", "current_chapter")

    def __init__(
        self,
        *,
        id: int,
        title: str,
        topic: str,
        mode: BookMode,
        genre: str,
        scale: str,
        target_chapters: int,
        total_volumes: int,
        status: str,
        current_volume: int,
        current_chapter: int,
    ) -> None:
        self.id = id
        self.title = title
        self.topic = topic
        self.mode = mode
        self.genre = genre
        self.scale = scale
        self.target_chapters = target_chapters
        self.total_volumes = total_volumes
        self.status = status
        self.current_volume = current_volume
        self.current_chapter = current_chapter

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"BookRecord(id={self.id}, mode={self.mode.value}, "
                f"genre={self.genre!r}, topic={self.topic[:20]!r})")


class BookRepository(Repository[BookRecord]):
    """`books` 表读写。"""

    model = BookRow

    def to_domain(self, row: Any) -> BookRecord:
        return BookRecord(
            id=int(row.id),
            title=str(row.title or ""),
            topic=str(row.topic or ""),
            mode=BookMode(str(row.mode)),
            genre=str(row.genre or ""),
            scale=str(row.scale or ""),
            target_chapters=int(row.target_chapters),
            total_volumes=int(row.total_volumes),
            status=str(row.status),
            current_volume=int(row.current_volume),
            current_chapter=int(row.current_chapter),
        )

    def find_by_id(self, book_id: int) -> BookRecord | None:
        return self.get(book_id)

    # ⚠️ 刻意**不提供** `list_by_mode()` 之类的模式维度查询。
    #
    # 契约规则 3 会拦下 `BookRow.mode == mode.value` 这类比较
    # （实测：写这个方法时被规则 3 当场拦下，
    #  `tests/contract/test_architecture_invariants.py` 的
    #  `test_rule03_no_mode_branching`）。
    # 规则 3 的白名单是**空集**（"白名单越少越安全"），
    # 所以正确的做法是**不加这个 API**，而不是给规则开例外 ——
    # 这也是决策 D-08「模式差异一律经 modes/ Protocol」的落地：
    # 需要按模式筛选时，由模式能力层自己组装查询条件，不在共享仓储里判模式。

    def upsert(
        self,
        *,
        book_id: int,
        topic: str,
        genre: str,
        mode: BookMode | str = BookMode.STORY,
        title: str = "",
        scale: str = "standard",
        target_chapters: int = 400,
    ) -> BookRecord:
        """按**显式 id** 插入或更新（幂等）。

        ★ 为什么接受显式 `book_id` 而不是自增：本项目的产物路径
        （`books/{id}/bible.md`）、`thread_id`、工作单元键**都以
        book_id 为前缀**，且 CLI 的 `--book-id` 是用户指定的。
        自增 id 会让"产物已存在但 DB 无行"的既有书无法补登记。

        `mode` 是本项目 **story / learn 的唯一持久化归属** ——
        此前它只写不读，本方法让它真正落库。
        """
        mode_v = mode.value if isinstance(mode, BookMode) else str(mode)
        with self.session() as sess:
            row = sess.get(BookRow, book_id)
            if row is None:
                row = BookRow(
                    id=book_id,
                    title=title or topic[:200],
                    topic=topic,
                    mode=mode_v,
                    genre=genre,
                    scale=scale,
                    target_chapters=int(target_chapters),
                    total_volumes=0,
                    status=BookStatus.CREATED.value,
                    current_volume=1,
                    current_chapter=0,
                )
                sess.add(row)
            else:
                # 已存在 → 只更新"用户重新指定"的字段，
                # **不重置进度**（current_chapter/status 是流水线的成果）
                row.topic = topic or row.topic
                row.genre = genre or row.genre
                row.mode = mode_v
                if title:
                    row.title = title
                if target_chapters:
                    row.target_chapters = int(target_chapters)
            sess.flush()
            return self.to_domain(row)

    def set_progress(
        self, book_id: int, *, volume_no: int | None = None,
        chapter_no: int | None = None, status: BookStatus | str | None = None,
    ) -> None:
        """更新进度（章号一律**全书递增号**，D-15）。"""
        with self.session() as sess:
            row = sess.get(BookRow, book_id)
            if row is None:
                raise KeyError(f"书 {book_id} 不存在")
            if volume_no is not None:
                row.current_volume = int(volume_no)
            if chapter_no is not None:
                row.current_chapter = int(chapter_no)
            if status is not None:
                row.status = status.value if isinstance(status, BookStatus) else str(status)
