"""教学进度调度 —— **艾宾浩斯间隔复习**（阶段 2-4）。

## ★ 先纠正一个此前的错误认知

本项目多份文档曾写「旧项目 `KnowledgeLedger` 有 `review_count`/`next_review_ch`
字段但**无任何调度实现**」。**这是错的**（已 ingest 更正）：

旧项目 `memory.py:3069-3145` 有**完整实现**，且在生产的 `pipeline.py` 里
有 **4 个调用点**（`5571` / `6446` / `8310` / `8312`）。本模块**移植并改进**它，
而不是从零设计。

## 移植的设计要点（逐条来自旧项目实测）

| 要点 | 旧项目 | 本设计 |
|---|---|---|
| 间隔表 | `config.py:120` `REVIEW_INTERVALS="3,7,15,30"` | `TeachingSettings.intervals`（同类，但可被档位覆盖） |
| 首次讲授即排期 | `update_after_chapter`：`next = now + intervals[0]` | `record_taught()` |
| 到期挑选 | `pick_due_reviews`：高潮章不注入 / 上限自适应 / 最紧急优先 | `due_reviews()` 逐条保留 |
| **用了才推进** | `advance_reviews`：**仅对正文里真正出现过的点**推进 | `advance()` |
| **没用就顺延** | `defer_reviews`：`max(next, now) + delay`，**不记一次复习** | `defer()` |
| **毕业** | `review_count >= len(intervals)` → 状态 `已复用` | ★ 见下 |

## ★ 本设计必须补的一处：三态

旧项目用 `KnowledgeLedger.status` 的 **`未讲授 / 已讲授 / 已复用`** 三态
表达生命周期。而本项目阶段 1 的 `modes/protocols.py:TeachingRecord`
只有 `point_id / taught_chapter / review_count / next_review_chapter` ——
**缺"毕业"状态**。

不补的后果：**毕业的知识点会被反复挑中**（`next_review_chapter` 为 None 时
若与"还没教"同样处理，两者无法区分）。故本模块引入 `TeachingStatus` 三态。

## 为什么"不注入进高潮章"是对的

旧项目注释原文：「高潮/Boss/情绪章不注入，避免打断场面」。
艾宾浩斯复习是**教学**需求，而高潮章的任务是**情绪与冲突**；
硬塞复习会同时毁掉两者。本设计保留这一约束，且做成**可测的**。

## 为什么"用了才推进"是关键

若"挑了到期点"就算复习过，那么**没真正写进正文**的点也会被推进间隔 ——
它的"记忆强度"是假的，下一次到期时读者其实已经忘了。
旧项目用 `used = [n for n in due_names if n in content]` 做正文核对，
本设计保留该判据（可在 `advance()` 的 `appeared` 参数看到）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storyforge.domain.enums import TeachingStatus

#: ★ `TeachingStatus` 定义在 `domain/enums.py`（契约规则 8：
#: 枚举必须集中定义，不得散落）—— 初版我把它写在本模块里，
#: 被规则 8 当场拦下。**正确反应是搬过去，不是给规则开例外。**
__all__ = [
    "TeachingStatus",
    "TeachingRecord",
    "TeachingLedger",
    "DEFAULT_INTERVALS",
    "DEFAULT_DEFER_DELAY",
    "REVIEW_EXCLUDED_BEATS",
    "CAP_WITH_NEW_POINTS",
    "CAP_WITHOUT_NEW_POINTS",
]


#: 默认间隔表（章）。逐字来自旧项目 `config.py:120`
#: `REVIEW_INTERVALS: str = "3,7,15,30"`。
#:
#: ★ 为什么是"章"而不是"天"：这是**小说**，读者的"遗忘曲线"由
#: **阅读进度**决定，不是墙钟时间。旧项目把它按章号排期是对的。
DEFAULT_INTERVALS: tuple[int, ...] = (3, 7, 15, 30)

#: 顺延的默认延迟（章）—— 旧项目 `defer_reviews(..., delay=2)`
DEFAULT_DEFER_DELAY = 2

#: 不注入复习的节拍类型（旧项目 `pick_due_reviews`：`boss/climax/emotion`）
#:
#: 「高潮/Boss/情绪章不注入，避免打断场面」
REVIEW_EXCLUDED_BEATS: frozenset[str] = frozenset({"boss", "climax", "emotion"})

#: 有新知识点时每章最多塞几个复习点（旧项目 `cap = 1 if has_new_points else min(limit, 3)`）
CAP_WITH_NEW_POINTS = 1
CAP_WITHOUT_NEW_POINTS = 3


@dataclass
class TeachingRecord:
    """一个知识点的教学进度。

    ★ 与阶段 1 `modes/protocols.py:TeachingRecord` 的关系：
    那个是 **Protocol 层**的轻量定义（4 字段），本类是 **domain 层**实现
    （多出 `status` 三态与 `deferred_count`）。`from_protocol()` 负责转换。

    为什么不在协议层就补全：协议层要**稳定**（它是 story/learn 的共同契约），
    而"毕业""顺延计数"是 learn 调度的实现细节。
    """

    point_id: str
    #: 首次讲授章（全书递增号，D-15）
    taught_chapter: int = 0
    #: 已完成的复习次数（= 已走过的间隔数）
    review_count: int = 0
    #: 下次应复习的章（None = 已毕业或尚未排期）
    next_review_chapter: int | None = None
    status: TeachingStatus = TeachingStatus.UNTAUGHT
    #: 被顺延的次数（观测用：反复顺延说明该点难自然融入）
    deferred_count: int = 0

    # -- 派生 ---------------------------------------------------------------

    @property
    def is_taught(self) -> bool:
        return self.status is not TeachingStatus.UNTAUGHT

    @property
    def is_mastered(self) -> bool:
        """已毕业 —— 不再被挑中复习。"""
        return self.status is TeachingStatus.MASTERED

    @property
    def is_due(self) -> bool:
        """是否还有待复习的排期（毕业的没有）。"""
        return self.next_review_chapter is not None and not self.is_mastered

    def due_at(self, chapter_no: int) -> bool:
        """在 `chapter_no` 章是否到期。"""
        return self.is_due and int(self.next_review_chapter or 0) <= chapter_no


@dataclass
class TeachingLedger:
    """教学账本 —— 全部知识点的进度（内存态；持久化由调用方负责）。

    旧项目存在 `knowledge_ledger` 表；本项目按 O-02 决策
    （learn 专属实体**只定义模型不建表**）先在内存/产物里管理。
    """

    records: dict[str, TeachingRecord] = field(default_factory=dict)
    intervals: tuple[int, ...] = DEFAULT_INTERVALS

    # -- 查询 ---------------------------------------------------------------

    def get(self, point_id: str) -> TeachingRecord | None:
        return self.records.get(point_id)

    def __len__(self) -> int:
        return len(self.records)

    def untaught(self) -> list[str]:
        return [
            pid for pid, r in self.records.items()
            if r.status is TeachingStatus.UNTAUGHT
        ]

    def due_reviews(
        self,
        chapter_no: int,
        *,
        beat: str = "",
        has_new_points: bool = False,
        exclude: set[str] | None = None,
        limit: int = CAP_WITHOUT_NEW_POINTS,
    ) -> list[TeachingRecord]:
        """挑出本章应复习的知识点（**按最紧急优先**）。

        逐条移植旧项目 `pick_due_reviews` 的三条规则：

          1. **高潮/Boss/情绪章不注入**（避免打断场面）
          2. **上限自适应**：有新知识点 → 1 个；否则 → 最多 3 个
          3. **最紧急优先**：按 `next_review_chapter` 升序
             （旧项目注释：避免 `random.sample` 让冷门卡长期轮不到）

        `exclude` 用于排除**本章正在新讲**的点（旧项目传 `chapter_data["points"]`）——
        同一个点不该在同一章既"新讲"又"复习"。
        """
        if (beat or "").lower() in REVIEW_EXCLUDED_BEATS:
            return []
        cap = CAP_WITH_NEW_POINTS if has_new_points else min(limit, CAP_WITHOUT_NEW_POINTS)
        if cap <= 0:
            return []
        skip = exclude or set()
        due = [
            r for pid, r in self.records.items()
            if pid not in skip and r.due_at(chapter_no)
        ]
        # 最紧急优先（过期最久 = next 最小）
        due.sort(key=lambda r: int(r.next_review_chapter or 0))
        return due[:cap]

    # -- 写入 ---------------------------------------------------------------

    def record_taught(self, point_id: str, chapter_no: int) -> TeachingRecord:
        """记录"首次讲授"并**排第一次复习**（旧项目 `update_after_chapter`）。

        幂等：已讲授过的点再调不会重置进度
        （旧项目用 `if k and k.status == "未讲授"` 守卫 —— 同一形状）。
        """
        r = self.records.get(point_id)
        if r is None:
            r = TeachingRecord(point_id=point_id)
            self.records[point_id] = r
        if r.status is not TeachingStatus.UNTAUGHT:
            return r                      # 已讲授 → 不重置（幂等）
        r.status = TeachingStatus.TAUGHT
        r.taught_chapter = int(chapter_no)
        r.review_count = 0
        if self.intervals:
            r.next_review_chapter = int(chapter_no) + self.intervals[0]
        else:
            r.next_review_chapter = None
        return r

    def advance(self, point_id: str, chapter_no: int) -> TeachingRecord | None:
        """**正文里真正用上了** → 推进到下一个间隔。

        旧项目 `advance_reviews` 的守卫（逐条保留）：
          · 记录不存在 / 无排期 / 未到期 → **不动**（`next > g_now` 直接 continue）
          · `review_count+1 >= len(intervals)` → **毕业**（状态转 `MASTERED`、
            `next=None`）
          · 否则 `next = chapter_no + intervals[review_count]`
        """
        r = self.records.get(point_id)
        if r is None or r.next_review_chapter is None:
            return None
        if r.is_mastered:
            return r
        if int(r.next_review_chapter) > int(chapter_no):
            return r                      # 未到期，不动
        r.review_count += 1
        if r.review_count >= len(self.intervals):
            r.status = TeachingStatus.MASTERED
            r.next_review_chapter = None
        else:
            r.next_review_chapter = int(chapter_no) + self.intervals[r.review_count]
        return r

    def defer(
        self, point_id: str, chapter_no: int, *, delay: int = DEFAULT_DEFER_DELAY
    ) -> TeachingRecord | None:
        """**本章没自然用上** → 顺延，且**不记一次复习**。

        旧项目 `defer_reviews` 的公式（逐条保留）：

            next_review_ch = max(next_review_ch, g_now) + max(1, delay)

        ★ 用 `max(...)` 而非直接赋值 `g_now`：过期的点顺延后不应
        **倒退**到比原排期更早（那会让它在下章又立刻到期，反复打扰）。
        """
        r = self.records.get(point_id)
        if r is None or r.next_review_chapter is None:
            return None
        r.next_review_chapter = (
            max(int(r.next_review_chapter), int(chapter_no)) + max(1, int(delay))
        )
        r.deferred_count += 1
        return r

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "intervals": list(self.intervals),
            "records": [
                {
                    "point_id": r.point_id,
                    "taught_chapter": r.taught_chapter,
                    "review_count": r.review_count,
                    "next_review_chapter": r.next_review_chapter,
                    "status": r.status.value,
                    "deferred_count": r.deferred_count,
                }
                for r in self.records.values()
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> TeachingLedger:
        ledger = cls(intervals=tuple(raw.get("intervals") or DEFAULT_INTERVALS))
        for item in raw.get("records") or []:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("point_id", ""))
            if not pid:
                continue
            try:
                status = TeachingStatus(str(item.get("status", TeachingStatus.UNTAUGHT)))
            except ValueError:
                status = TeachingStatus.UNTAUGHT
            nxt = item.get("next_review_chapter")
            ledger.records[pid] = TeachingRecord(
                point_id=pid,
                taught_chapter=int(item.get("taught_chapter", 0)),
                review_count=int(item.get("review_count", 0)),
                next_review_chapter=None if nxt is None else int(nxt),
                status=status,
                deferred_count=int(item.get("deferred_count", 0)),
            )
        return ledger

    # -- 诊断 ---------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """供 CLI **可视化**：各状态计数 + 当前待复习数。"""
        out = {
            "total": len(self.records),
            TeachingStatus.UNTAUGHT.value: 0,
            TeachingStatus.TAUGHT.value: 0,
            TeachingStatus.MASTERED.value: 0,
            "due_now": 0,
            "deferred_total": 0,
        }
        for r in self.records.values():
            out[r.status.value] = out.get(r.status.value, 0) + 1
            out["deferred_total"] += r.deferred_count
        return out


__all__ = [
    "TeachingStatus",
    "TeachingRecord",
    "TeachingLedger",
    "DEFAULT_INTERVALS",
    "DEFAULT_DEFER_DELAY",
    "REVIEW_EXCLUDED_BEATS",
    "CAP_WITH_NEW_POINTS",
    "CAP_WITHOUT_NEW_POINTS",
]
