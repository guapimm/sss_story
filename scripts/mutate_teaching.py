"""变异测试：证明艾宾浩斯调度的**每条规则**都真的被测试钉住。

## 为什么这个文件尤其重要

阶段 2-4 是**移植**旧项目已验证的实现（`memory.py:3069-3145`）。
移植最典型的失败是"**看起来一样但不等价**" ——
少一个 `max()`、少一个守卫，行为就变了，而普通测试可能仍绿。

本脚本对**五条关键规则**各做一次真变异：

  变异 1：撤掉"未到期不推进"守卫（`next > chapter` 直通）
          → 复习间隔被人为压缩（艾宾浩斯名存实亡）
  变异 2：撤掉"高潮章不注入"（beat 判断）
          → 高潮场面被打断
  变异 3：把顺延的 `max(next, now)` 改成直接 `now`
          → 已过期的排期**倒退**（旧项目特意用 max 防这个）
  变异 4：让顺延也记一次复习（`review_count += 1`）
          → "没写进正文"的点假装复习过了
  变异 5：撤掉毕业（`next` 永不为 None）
          → 毕业的点被反复挑中

⚠️ 每条变异都改**被测试真正读取的那一行**（本项目吃过"假变异"的亏：
`scripts/mutate_rule03.py` 的 docstring 记着那次教训）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/storyforge/domain/teaching.py"

TESTS = {
    "not_due": "tests/unit/test_teaching.py::test_advance_does_nothing_before_due",
    "climax": "tests/unit/test_teaching.py::test_climax_chapters_get_no_reviews",
    "defer_no_back": "tests/unit/test_teaching.py::test_defer_does_not_move_backwards",
    "defer_no_count": "tests/unit/test_teaching.py::test_defer_postpones_without_counting",
    "graduate": "tests/unit/test_teaching.py::test_mastered_points_never_picked",
}

#: 变异 1：未到期守卫
GUARD_ANCHOR = "        if int(r.next_review_chapter) > int(chapter_no):\n            return r                      # 未到期，不动\n"
#: 变异 2：高潮章不注入
BEAT_ANCHOR = '        if (beat or "").lower() in REVIEW_EXCLUDED_BEATS:\n            return []\n'
#: 变异 3：顺延的 max 保护
MAX_ANCHOR = """        r.next_review_chapter = (
            max(int(r.next_review_chapter), int(chapter_no)) + max(1, int(delay))
        )
"""
#: 变异 4：顺延时不该记复习（在 defer 里加一行）
DEFER_ANCHOR = "        r.deferred_count += 1\n"
#: 变异 5：毕业
GRADUATE_ANCHOR = """        if r.review_count >= len(self.intervals):
            r.status = TeachingStatus.MASTERED
            r.next_review_chapter = None
        else:
"""


def _run(test_id: str) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-500:]


def _mutate(old: str, new: str, *, label: str, test_key: str,
            results: list) -> None:
    print("=" * 76)
    print(label)
    print("=" * 76)
    src = TARGET.read_text(encoding="utf-8")
    assert old in src, f"锚点未找到：{old.strip()[:60]}"
    TARGET.write_text(src.replace(old, new, 1), encoding="utf-8")
    caught, out = _run(TESTS[test_key])
    TARGET.write_text(src, encoding="utf-8")        # 立即还原
    print(f"  {'拦下 ✓' if caught else '★ 没拦下（规则没被钉住）'}")
    if not caught:
        print(out)
    results.append(caught)


def main() -> int:
    results: list[bool] = []

    _mutate(
        GUARD_ANCHOR, "",
        label="变异 1：撤掉「未到期不推进」守卫\n"
              "        → 复习间隔被人为压缩（艾宾浩斯名存实亡）",
        test_key="not_due", results=results,
    )
    _mutate(
        BEAT_ANCHOR, "",
        label="变异 2：撤掉「高潮章不注入复习」\n"
              "        → 高潮场面被打断（旧项目特意加的约束）",
        test_key="climax", results=results,
    )
    _mutate(
        MAX_ANCHOR,
        "        r.next_review_chapter = int(chapter_no) + max(1, int(delay))\n",
        label="变异 3：顺延改用 `now` 而非 `max(next, now)`\n"
              "        → 已过期的排期**倒退**（本该立刻复习的又被推后）",
        test_key="defer_no_back", results=results,
    )
    _mutate(
        DEFER_ANCHOR, "        r.deferred_count += 1\n        r.review_count += 1\n",
        label="变异 4：让顺延也记一次复习\n"
              "        → 「没写进正文」的点假装复习过了（记忆强度是假的）",
        test_key="defer_no_count", results=results,
    )
    _mutate(
        GRADUATE_ANCHOR + "            r.next_review_chapter = int(chapter_no) + self.intervals[r.review_count]\n",
        "        r.next_review_chapter = int(chapter_no) + self.intervals[min(r.review_count, len(self.intervals) - 1)]\n",
        label="变异 5：撤掉「毕业」（next 永不为 None）\n"
              "        → 毕业的点被反复挑中（本项补三态要解决的正是这个）",
        test_key="graduate", results=results,
    )

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    oks = [_run(t)[0] for t in TESTS.values()]
    all_clear = not any(oks)
    print(f"  五条测试 {'★ 仍在失败（还原失败）' if not all_clear else '全部通过 ✓'}")

    print()
    print("=" * 76)
    names = ("未到期守卫", "高潮章排除", "顺延不倒退", "顺延不记复习", "毕业")
    for name, c in zip(names, results):
        print(f"  {'✓' if c else '★'} {name} 被拦下")
    print(f"  {'✓' if all_clear else '★'} 还原后复验通过")
    return 0 if (all(results) and all_clear) else 1


if __name__ == "__main__":
    raise SystemExit(main())
