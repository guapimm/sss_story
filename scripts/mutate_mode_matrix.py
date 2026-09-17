"""变异测试：证明模式能力矩阵**双向都真的在测**。

本文件防的是旧项目那个**方向反了**的形态：
story 侧给 learn 的钩子写空实现（`story_flow.py:171-201`，8 个）。

三次真变异（改的都是被测试真正读取的属性）：

  变异 1：给 story 加一个 learn 专属能力的**空实现属性**
          → 复现旧项目的失败形态 → 矩阵测试必须失败
  变异 2：给 learn 加一个 learn 专属能力的属性
          → 破坏"一期不挂属性"→ 矩阵测试必须失败
  变异 3：从 `mode_for` 的登记表里删掉 learn
          → 破坏"每个 BookMode 都能取到" → 覆盖测试必须失败
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORY = ROOT / "src/storyforge/modes/story.py"
LEARN = ROOT / "src/storyforge/modes/learn.py"
REGISTRY = ROOT / "src/storyforge/modes/registry.py"

TEST_STORY = ("tests/contract/test_mode_capabilities.py::"
              "test_story_implements_exactly_shared_capabilities")
TEST_LEARN = ("tests/contract/test_mode_capabilities.py::"
              "test_learn_does_not_implement_story_side_either")
TEST_COVER = ("tests/contract/test_mode_capabilities.py::"
              "test_mode_for_covers_every_book_mode")

#: 变异 1 的锚点：`StoryMode.__init__` 的最后一行（在 **registry.py**）
STORY_ANCHOR = "        self.estimator = StoryEstimator()\n"
#: 变异 2 的锚点：`LearnMode.__init__` 的流派插件行
LEARN_ANCHOR = "        self.genre = _UnimplementedGenre()\n"
#: 变异 3 的锚点：登记表里的 learn 行
REGISTRY_ANCHOR = '    BookMode.LEARN.value: _build_learn,\n'


def _run(test_id: str) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-700:]


def _mutate(path: Path, old: str, new: str, *, label: str, test_id: str,
            results: list) -> None:
    print("=" * 76)
    print(label)
    print("=" * 76)
    src = path.read_text(encoding="utf-8")
    assert old in src, f"锚点未找到：{path.name}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    caught, out = _run(test_id)
    path.write_text(src, encoding="utf-8")          # 立即还原
    print(f"  {'拦下 ✓' if caught else '★ 没拦下（矩阵有洞）'}")
    if not caught:
        print(out)
    results.append(caught)


def main() -> int:
    results: list[bool] = []

    # ---- 变异 1：story 给 learn 专属能力加空实现（旧项目的形态）----
    # ⚠️ `StoryMode` 定义在 `registry.py`（不是 story.py）—— 它是
    #    "模式对象"的组合点，与"story 的能力实现"分文件。
    _mutate(
        REGISTRY,
        STORY_ANCHOR,
        STORY_ANCHOR + "        self.ledger = _EmptyLedger()   # 变异：旧项目形态\n",
        label="变异 1：给 story 加 learn 专属能力 `ledger` 的空实现\n"
              "        （这正是旧项目 story_flow.py:171-201 的 8 个 @staticmethod 空实现）",
        test_id=TEST_STORY,
        results=results,
    )

    # ---- 变异 2：learn 给专属能力挂属性 ----
    _mutate(
        LEARN,
        LEARN_ANCHOR,
        LEARN_ANCHOR + "        self.ledger = _UnimplementedLedger()  # 变异\n",
        label="变异 2：给 learn 挂上 `ledger` 属性\n"
              "        （破坏'取的时候就炸'，退化成'调用时才炸'）",
        test_id=TEST_LEARN,
        results=results,
    )

    # ---- 变异 3：登记表漏一个模式 ----
    _mutate(
        REGISTRY,
        REGISTRY_ANCHOR,
        "",
        label="变异 3：从 `_BUILDERS` 删掉 learn 登记\n"
              "        （同类缺陷：PipelineStage.CHAPTER 在枚举有、映射没有）",
        test_id=TEST_COVER,
        results=results,
    )

    # ---- 还原复验 ----
    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok1, _ = _run(TEST_STORY)
    ok2, _ = _run(TEST_LEARN)
    ok3, _ = _run(TEST_COVER)
    all_clear = not (ok1 or ok2 or ok3)
    print(f"  三条测试 {'★ 仍在失败（还原失败）' if not all_clear else '全部通过 ✓'}")

    print()
    print("=" * 76)
    for name, c in zip(("story 空实现 learn 能力", "learn 挂专属能力属性",
                        "登记表漏模式"), results):
        print(f"  {'✓' if c else '★'} {name} 被拦下")
    print(f"  {'✓' if all_clear else '★'} 还原后复验通过")
    return 0 if (all(results) and all_clear) else 1


if __name__ == "__main__":
    raise SystemExit(main())
