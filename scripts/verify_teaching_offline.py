"""2-4 教学账本 / 艾宾浩斯调度的**离线**端到端验证（不联网、不花钱）。

跑一遍完整生命周期，把每一步的排期与状态变化打出来 ——
用于人工确认"移植的行为与旧项目一致"。

## 与单测的分工

  · `tests/unit/test_teaching.py`（27 条）钉住**每一条规则的边界**
  · 本脚本演示**完整时间线**，便于人眼核对"读起来对不对"

用法：`uv run python scripts/verify_teaching_offline.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.config import get_settings  # noqa: E402
from storyforge.domain.teaching import (  # noqa: E402
    REVIEW_EXCLUDED_BEATS,
    TeachingLedger,
    TeachingStatus,
)


def main() -> int:
    s = get_settings().teaching
    intervals = tuple(s.intervals)
    L = TeachingLedger(intervals=intervals)

    print("=" * 76)
    print("① 配置（逐条来自旧项目 config.py:120 / defer_reviews(delay=2)）")
    print("=" * 76)
    print(f"  复习间隔（章）：{list(intervals)}  ← 走完全部 = 毕业")
    print(f"  顺延延迟      ：{s.defer_delay} 章")
    print(f"  不注入的节拍  ：{s.excluded_beats}")

    print()
    print("=" * 76)
    print("② 完整生命周期：第 1 章讲授 → 依次复习 → 毕业")
    print("=" * 76)
    L.record_taught("kp001", 1)
    r = L.get("kp001")
    print(f"  第 1 章讲授      → 状态={r.status.value}  下次@第 {r.next_review_chapter} 章")

    review_chapters = [1 + intervals[0]]
    for i in range(1, len(intervals)):
        review_chapters.append(review_chapters[-1] + intervals[i])
    for ch in review_chapters:
        due = [x.point_id for x in L.due_reviews(ch)]
        L.advance("kp001", ch)
        r = L.get("kp001")
        state = r.status.value
        nxt = r.next_review_chapter if r.next_review_chapter is not None else "—"
        print(f"  第 {ch:>2} 章复习      → 选中={due}  状态={state:<4} "
              f"已复习 {r.review_count} 次  下次@{nxt}")

    print()
    print(f"  毕业后再查（第 200 章）：{L.due_reviews(200)}（应为空）")

    print()
    print("=" * 76)
    print("③ 三条挑选规则")
    print("=" * 76)
    L2 = TeachingLedger(intervals=intervals)
    L2.record_taught("early", 1)      # next = 4
    L2.record_taught("mid", 5)        # next = 8
    L2.record_taught("late", 20)      # next = 23
    print("  三个点分别在第 1/5/20 章讲授")
    picked = [x.point_id for x in L2.due_reviews(30)]
    print(f"  第 30 章挑选 → {picked}")
    print("     · 最紧急优先（next 升序）：early(4) < mid(8) < late(23) ✓")
    print(f"     · 上限 3（无新知识点）：{len(picked)} 个")
    picked_new = [x.point_id for x in L2.due_reviews(30, has_new_points=True)]
    print(f"  若有新知识点 → {picked_new}（上限降为 1）")
    for beat in sorted(REVIEW_EXCLUDED_BEATS):
        got = L2.due_reviews(30, beat=beat)
        print(f"  {beat:<8} 章 → {got}（不注入，避免打断场面）")

    print()
    print("=" * 76)
    print("④ 顺延（没自然用上）")
    print("=" * 76)
    L3 = TeachingLedger(intervals=intervals)
    L3.record_taught("p", 1)
    before = L3.get("p").next_review_chapter
    L3.defer("p", 4, delay=s.defer_delay)
    r3 = L3.get("p")
    print(f"  排期 @第 {before} 章 → 第 4 章未用上 → 顺延到 @第 {r3.next_review_chapter} 章")
    print(f"  复习次数仍为 {r3.review_count}（**顺延不记复习**）")
    print(f"  顺延计数 {r3.deferred_count}（用于观测'哪些点难自然融入'）")

    print()
    print("=" * 76)
    print("⑤ 三态统计（CLI 可视化用）")
    print("=" * 76)
    import json
    print("  " + json.dumps(L.stats(), ensure_ascii=False))
    print("  " + json.dumps(L3.stats(), ensure_ascii=False))

    print()
    print("=" * 76)
    ok = (
        L.get("kp001").status is TeachingStatus.MASTERED
        and L.get("kp001").review_count == len(intervals)
        and picked == ["early", "mid", "late"]
        and r3.review_count == 0
    )
    print(f"  结论：{'✓ 全部符合旧项目语义' if ok else '★ 有偏差'}")
    print("=" * 76)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
