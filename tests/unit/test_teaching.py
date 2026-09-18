"""教学账本 + 艾宾浩斯复习调度（阶段 2-4）测试。

## ★ 先说一个此前的错误认知（本文件的价值之一）

本项目多份文档曾写「旧项目 `KnowledgeLedger` 有 `review_count`/`next_review_ch`
字段但**无任何调度实现**」。**这是错的** —— 实测
`memory.py:3069-3145` 有完整实现，且在生产的 `pipeline.py` 里有 **4 个调用点**。
已 ingest 更正文档。

因此本文件的定位**不是"从零设计"**，而是**移植并钉住旧项目已验证的规则**：

| 规则 | 来源 |
|---|---|
| 间隔 `3,7,15,30`（**章**，不是天） | `config.py:120` |
| 首次讲授即排第一次复习 | `memory.py:3052-3064` |
| **高潮/Boss/情绪章不注入** | `pick_due_reviews` 的 `beat` 判断 |
| **上限自适应**：有新点→1，否则→≤3 | `cap = 1 if has_new_points else min(limit,3)` |
| **最紧急优先**（按 next 升序） | 注释：避免冷门卡长期轮不到 |
| **用了才推进** | `advance_reviews` 只对 `used` 调用 |
| **没用就顺延**，且不记复习 | `defer_reviews` 的 `max(next, now)+delay` |
| **毕业**：走完全部间隔 | `review_count >= len(intervals)` → `已复用` |

★ 最后一条是本项目**必须补的缺口**：阶段 1 的 `TeachingRecord` 只有 4 字段、
无三态，不补会让"毕业"与"还没教"无法区分 → 毕业的知识点被反复挑中。
"""

from __future__ import annotations

import pytest

from storyforge.config import get_settings
from storyforge.domain.teaching import (
    CAP_WITHOUT_NEW_POINTS,
    CAP_WITH_NEW_POINTS,
    DEFAULT_DEFER_DELAY,
    DEFAULT_INTERVALS,
    REVIEW_EXCLUDED_BEATS,
    TeachingLedger,
    TeachingStatus,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def ledger() -> TeachingLedger:
    return TeachingLedger(intervals=DEFAULT_INTERVALS)


# --------------------------------------------------------------------------- #
# 间隔表（移植自旧项目 config）
# --------------------------------------------------------------------------- #


def test_default_intervals_match_old_project() -> None:
    """★ 间隔表必须与旧项目 `REVIEW_INTERVALS="3,7,15,30"` 一致。

    改它等于改"复习强度"—— 属于行为变更，不该顺手做。
    """
    assert DEFAULT_INTERVALS == (3, 7, 15, 30)


def test_config_intervals_match_module_default() -> None:
    """配置与模块默认必须同源（防两处漂移）。"""
    assert tuple(get_settings().teaching.intervals) == DEFAULT_INTERVALS


def test_intervals_are_increasing() -> None:
    """间隔必须递增 —— 否则"越来越稀疏"的遗忘曲线不成立。"""
    assert list(DEFAULT_INTERVALS) == sorted(DEFAULT_INTERVALS)
    assert len(set(DEFAULT_INTERVALS)) == len(DEFAULT_INTERVALS)


# --------------------------------------------------------------------------- #
# 首次讲授
# --------------------------------------------------------------------------- #


def test_record_taught_schedules_first_review(ledger) -> None:
    """★ 首次讲授即排第一次复习（旧项目 `update_after_chapter`）。"""
    r = ledger.record_taught("kp1", 1)
    assert r.status is TeachingStatus.TAUGHT
    assert r.taught_chapter == 1
    assert r.review_count == 0
    assert r.next_review_chapter == 1 + DEFAULT_INTERVALS[0]


def test_record_taught_is_idempotent(ledger) -> None:
    """★★ 重复讲授**不得重置进度**（旧项目用 `status == "未讲授"` 守卫）。

    不幂等的后果：重跑一章会把"已经复习过 3 次"的点打回原形。
    """
    ledger.record_taught("kp1", 1)
    ledger.advance("kp1", 4)          # 复习 1 次
    before = ledger.get("kp1")
    assert before.review_count == 1

    ledger.record_taught("kp1", 99)   # 再"讲授"一次
    after = ledger.get("kp1")
    assert after.review_count == 1, "复习次数被重置了"
    assert after.taught_chapter == 1, "首次讲授章被改写了"
    assert after.next_review_chapter == before.next_review_chapter


def test_untaught_points_listed(ledger) -> None:
    ledger.record_taught("kp1", 1)
    ledger.records["kp2"] = ledger.records["kp1"].__class__(point_id="kp2")
    assert ledger.untaught() == ["kp2"]


# --------------------------------------------------------------------------- #
# 到期挑选（三条规则逐条钉住）
# --------------------------------------------------------------------------- #


def test_due_only_after_interval(ledger) -> None:
    """未到期不挑（`next_review_chapter <= chapter_no` 才到期）。"""
    ledger.record_taught("kp1", 1)          # next = 4
    assert ledger.due_reviews(3) == []
    assert [r.point_id for r in ledger.due_reviews(4)] == ["kp1"]


def test_climax_chapters_get_no_reviews(ledger) -> None:
    """★★ **高潮/Boss/情绪章不注入复习**（旧项目：「避免打断场面」）。

    这是"教学需求"让位于"阅读体验"的设计决定 —— 硬塞复习会同时
    毁掉高潮的节奏与复习的效果。
    """
    ledger.record_taught("kp1", 1)
    for beat in sorted(REVIEW_EXCLUDED_BEATS):
        assert ledger.due_reviews(4, beat=beat) == [], f"{beat} 章不该注入复习"
    # 对照：普通章要能挑到
    assert [r.point_id for r in ledger.due_reviews(4, beat="normal")] == ["kp1"]


def test_cap_adapts_to_new_points(ledger) -> None:
    """★ **上限自适应**：有新知识点 → 1 个；否则 → 最多 3 个。

    旧项目 `cap = 1 if has_new_points else min(limit, 3)`。
    理由：本章已经有新内容要讲，再塞多个复习点会挤掉主线。
    """
    for i in range(5):
        ledger.record_taught(f"kp{i}", 1)
    assert len(ledger.due_reviews(4, has_new_points=True)) == CAP_WITH_NEW_POINTS
    assert len(ledger.due_reviews(4, has_new_points=False)) == CAP_WITHOUT_NEW_POINTS
    assert CAP_WITH_NEW_POINTS == 1
    assert CAP_WITHOUT_NEW_POINTS == 3


def test_most_overdue_first(ledger) -> None:
    """★★ **最紧急优先**（按 `next_review_chapter` 升序）。

    旧项目注释原文：避免 `random.sample` 让冷门卡**长期轮不到**。
    用随机挑选会让"复习公平性"无法保证。
    """
    # 三个点在不同章到期，但都在第 20 章已过期
    ledger.record_taught("late", 10)      # next=13
    ledger.record_taught("early", 1)      # next=4
    ledger.record_taught("mid", 5)        # next=8
    picked = [r.point_id for r in ledger.due_reviews(20)]
    assert picked == ["early", "mid", "late"], (
        f"未按最紧急优先排序：{picked}（应 early(4) < mid(8) < late(13)）"
    )


def test_exclude_prevents_taught_and_reviewed_same_chapter(ledger) -> None:
    """★ `exclude` 排除**本章正在新讲**的点。

    同一点不该在同一章既"新讲"又"复习"（旧项目传 `chapter_data["points"]`）。
    """
    ledger.record_taught("kp1", 1)
    assert ledger.due_reviews(4, exclude={"kp1"}) == []
    assert [r.point_id for r in ledger.due_reviews(4)] == ["kp1"]


def test_mastered_points_never_picked(ledger) -> None:
    """★★ **毕业的点不再被挑中** —— 这是本项目补的三态的直接价值。

    本项目阶段 1 的 `TeachingRecord` 无三态，若不补：
    `next_review_chapter=None` 会与"尚未排期"混淆。
    """
    ledger.record_taught("kp1", 1)
    for ch in (4, 11, 26, 56):
        ledger.advance("kp1", ch)
    assert ledger.get("kp1").status is TeachingStatus.MASTERED
    assert ledger.get("kp1").is_mastered is True
    assert ledger.due_reviews(1000) == []


# --------------------------------------------------------------------------- #
# 推进（用了才推进）
# --------------------------------------------------------------------------- #


def test_advance_walks_through_all_intervals(ledger) -> None:
    """★ 走完全部间隔 → 毕业（`review_count >= len(intervals)`）。"""
    ledger.record_taught("kp1", 1)
    expected_next = [1 + DEFAULT_INTERVALS[0]]
    for i, ch in enumerate((4, 11, 26), start=1):
        ledger.advance("kp1", ch)
        r = ledger.get("kp1")
        assert r.review_count == i
        assert r.status is TeachingStatus.TAUGHT
        expected_next.append(
            ch + DEFAULT_INTERVALS[i] if i < len(DEFAULT_INTERVALS) else None
        )
    # 最后一次：第 56 章推进 → 毕业
    ledger.advance("kp1", 56)
    r = ledger.get("kp1")
    assert r.review_count == len(DEFAULT_INTERVALS)
    assert r.status is TeachingStatus.MASTERED
    assert r.next_review_chapter is None


def test_advance_does_nothing_before_due(ledger) -> None:
    """★★ **未到期不推进** —— 旧项目 `if next_review_ch > g_now: continue`。

    不守这条的后果：复习间隔被人为压缩，"艾宾浩斯"名存实亡。
    """
    ledger.record_taught("kp1", 1)         # next = 4
    ledger.advance("kp1", 2)               # 还没到期
    r = ledger.get("kp1")
    assert r.review_count == 0, "未到期却推进了复习次数"
    assert r.next_review_chapter == 4, "未到期却改动了排期"


def test_advance_unknown_point_returns_none(ledger) -> None:
    assert ledger.advance("不存在", 5) is None


def test_advance_on_mastered_is_noop(ledger) -> None:
    """已毕业的点再 advance 不动（幂等）。"""
    ledger.record_taught("kp1", 1)
    for ch in (4, 11, 26, 56):
        ledger.advance("kp1", ch)
    ledger.advance("kp1", 100)
    r = ledger.get("kp1")
    assert r.status is TeachingStatus.MASTERED
    assert r.review_count == len(DEFAULT_INTERVALS)


# --------------------------------------------------------------------------- #
# 顺延（没用就顺延）
# --------------------------------------------------------------------------- #


def test_defer_postpones_without_counting(ledger) -> None:
    """★★ **顺延不记一次复习** —— 旧项目 `defer_reviews` 的核心语义。

    若顺延也记复习，那么"没写进正文"的点会假装复习过了，
    它的记忆强度是假的。
    """
    ledger.record_taught("kp1", 1)         # next = 4
    ledger.defer("kp1", 4, delay=DEFAULT_DEFER_DELAY)
    r = ledger.get("kp1")
    assert r.review_count == 0, "顺延不该记复习次数"
    assert r.next_review_chapter == 4 + DEFAULT_DEFER_DELAY
    assert r.status is TeachingStatus.TAUGHT


def test_defer_does_not_move_backwards(ledger) -> None:
    """★★ 顺延用 `max(next, now)` —— 防止排期**倒退**。

    ⚠️ 这条测试初版只覆盖了"已过期"的情形，**被变异测试当场发现没钉住**：
    当 `next=4, now=20` 时，`max(4,20)+2` 与 `20+2` **都得 22** ——
    两种实现无法区分，撤掉 `max()` 测试仍然全绿。

    真正能区分的是**反向**：`next` 在未来时，`max()` 会**保留**它，
    而直接赋值 `now + delay` 会把它**提前**（这才是"倒退"）。
    """
    # ① 已过期：顺延后不应早于 now（这是原有用例，判据弱）
    ledger.record_taught("overdue", 1)          # next = 4
    ledger.defer("overdue", 20, delay=2)
    assert ledger.get("overdue").next_review_chapter == 22

    # ② ★ 关键：next 在**未来**（30）且当前章更早（20）→ 必须保留 30+2
    ledger.record_taught("future", 27)          # next = 27 + 3 = 30
    assert ledger.get("future").next_review_chapter == 30
    ledger.defer("future", 20, delay=2)
    assert ledger.get("future").next_review_chapter == 32, (
        "顺延把**未来**的排期提前了 —— "
        "`max(next, now)` 被改成 `now` 会丢掉原本更晚的排期"
    )


def test_defer_never_advances_schedule(ledger) -> None:
    """★ 顺延**只能推后，不能提前**（对任意 now/next 组合都成立）。

    这是 `max(next, now)` 的完整不变式 —— 用一组组合穷举验证，
    比单个用例更难被"碰巧通过"。
    """
    cases = [(1, 4, 2, 6), (1, 10, 2, 12), (27, 20, 2, 32), (1, 50, 5, 55)]
    for taught_ch, defer_ch, delay, want in cases:
        lg = TeachingLedger(intervals=DEFAULT_INTERVALS)
        lg.record_taught("p", taught_ch)
        before = lg.get("p").next_review_chapter
        lg.defer("p", defer_ch, delay=delay)
        got = lg.get("p").next_review_chapter
        assert got == want, f"taught={taught_ch} defer@{defer_ch} → {got}，应为 {want}"
        assert got > before, (
            f"顺延后 {got} 不大于原排期 {before} —— 排期被提前了"
        )


def test_defer_counts_deferrals(ledger) -> None:
    """顺延次数要记（观测"哪些点难自然融入"）。"""
    ledger.record_taught("kp1", 1)
    for ch in range(4, 9):
        ledger.defer("kp1", ch)
    assert ledger.get("kp1").deferred_count == 5


def test_defer_minimum_delay_is_one(ledger) -> None:
    """`max(1, delay)`：delay 为 0 或负数时至少推后 1 章（否则下章又到期，反复打扰）。"""
    ledger.record_taught("kp1", 1)
    ledger.defer("kp1", 4, delay=0)
    assert ledger.get("kp1").next_review_chapter == 5
    ledger2 = TeachingLedger(intervals=DEFAULT_INTERVALS)
    ledger2.record_taught("kp2", 1)
    ledger2.defer("kp2", 4, delay=-5)
    assert ledger2.get("kp2").next_review_chapter == 5


def test_defer_unknown_point_returns_none(ledger) -> None:
    assert ledger.defer("不存在", 5) is None


# --------------------------------------------------------------------------- #
# 序列化（跨会话）
# --------------------------------------------------------------------------- #


def test_ledger_roundtrip(ledger) -> None:
    """★ 账本必须能序列化/还原（进度要跨会话保留）。"""
    ledger.record_taught("kp1", 1)
    ledger.advance("kp1", 4)
    ledger.record_taught("kp2", 5)
    ledger.defer("kp2", 12)
    ledger.advance("kp1", 11)

    restored = TeachingLedger.from_dict(ledger.to_dict())
    assert len(restored) == len(ledger)
    for pid in ("kp1", "kp2"):
        a, b = ledger.get(pid), restored.get(pid)
        assert (a.status, a.review_count, a.next_review_chapter,
                a.taught_chapter, a.deferred_count) == (
            b.status, b.review_count, b.next_review_chapter,
            b.taught_chapter, b.deferred_count)


def test_ledger_from_dict_tolerates_bad_status(ledger) -> None:
    """未知状态值 → 回落 `UNTAUGHT`（不炸；坏数据不该让账本读不出来）。"""
    restored = TeachingLedger.from_dict({
        "intervals": [3],
        "records": [{"point_id": "x", "status": "不存在的状态"}],
    })
    assert restored.get("x").status is TeachingStatus.UNTAUGHT


def test_ledger_from_dict_skips_malformed(ledger) -> None:
    restored = TeachingLedger.from_dict({
        "records": [{"no_point_id": 1}, "不是字典", {"point_id": "ok"}],
    })
    assert list(restored.records) == ["ok"]


# --------------------------------------------------------------------------- #
# 可视化（用户要求"全流程可视化"）
# --------------------------------------------------------------------------- #


def test_stats_reports_all_three_states(ledger) -> None:
    """★ `stats()` 要给出三态计数（供 CLI 展示进度）。"""
    ledger.record_taught("kp1", 1)
    ledger.record_taught("kp2", 1)
    ledger.advance("kp2", 4)               # kp2 复习 1 次
    ledger.records["kp3"] = ledger.records["kp1"].__class__(point_id="kp3")

    st = ledger.stats()
    assert st["total"] == 3
    assert st[TeachingStatus.UNTAUGHT.value] == 1
    assert st[TeachingStatus.TAUGHT.value] == 2
    assert st[TeachingStatus.MASTERED.value] == 0
    assert st["deferred_total"] == 0


def test_stats_counts_mastered_after_graduation(ledger) -> None:
    ledger.record_taught("kp1", 1)
    for ch in (4, 11, 26, 56):
        ledger.advance("kp1", ch)
    st = ledger.stats()
    assert st[TeachingStatus.MASTERED.value] == 1
    assert st[TeachingStatus.TAUGHT.value] == 0
