"""变异测试：证明 learn 打分卡的维度**真的在起作用**。

## 为什么必须做

learn 卡的最大风险不是"算错分"，而是：
  · 维度**没生效**（权重算进分母却恒通过）→ 卡在界面上绿灯
  · 退回 story 的维度集合（`dialogue_low` 又开始误杀）

★ 本项目已栽过三次「抓不到问题的检查等于没有」：
规则 3 四类漏检、**规则 22 完全失效**、`validate_map` 只查"有没有"。
故对 learn 卡也做变异。

## 五个变异（每个都模拟一种真实退化）

  变异 1：`substitution_rate` 空分母返回 1.0
          → 复现"没讲任何知识点 = 平替全对"的**假绿**
  变异 2：`bare_term` 阈值调成 999（等于永不触发）
          → 复现"平替确认流程白做"（learn 的核心职责失效）
  变异 3：learn 维度表混入 story 的 `dialogue_ratio`（下限语义）
          → 复现"教学章被 dialogue_low 误杀"（本项要修的原病）
  变异 4：`count_bare_terms` 不剥掉说法
          → 复现"说法含术语时自我误报"
  变异 5：`count_term_usage` 把未提到的知识点也计入分母
          → 复现**分母造假**（任何一章命中率都被拉低）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEARN = ROOT / "src/storyforge/scorecard/learn.py"

TEST_ID = "tests/unit/test_scorecard_learn.py"


def _run() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_ID, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-600:]


def _case(title: str, mutate) -> bool:
    original = LEARN.read_text(encoding="utf-8")
    mutated = mutate(original)
    assert mutated != original, f"变异「{title}」没有改动文件"
    LEARN.write_text(mutated, encoding="utf-8")
    caught, out = _run()
    LEARN.write_text(original, encoding="utf-8")     # 立即还原
    assert LEARN.read_text(encoding="utf-8") == original, "还原失败！"

    print(f"  {'✓ 拦下' if caught else '★ 放行（测试无效）'}  {title}")
    if caught:
        for line in out.splitlines():
            if "assert" in line or "Error" in line:
                print(f"        {line.strip()[:100]}")
                break
    return caught


def main() -> int:
    results: list[bool] = []

    print("=" * 76)
    print("变异 1：空分母的平替命中率算成 1.0（假绿）")
    print("=" * 76)
    results.append(_case(
        "空分母 → 1.0",
        lambda s: s.replace(
            "        if self.substitution_expected <= 0:\n            return None",
            "        if self.substitution_expected <= 0:\n            return 1.0", 1),
    ))

    print()
    print("=" * 76)
    print("变异 2：bare_term 阈值调成 999（永不触发）")
    print("=" * 76)
    results.append(_case(
        "bare_term 永不触发",
        lambda s: s.replace(
            "    hard_bare_term_hits: int = 3",
            "    hard_bare_term_hits: int = 999", 1),
    ))

    print()
    print("=" * 76)
    print("变异 3：learn 维度表混入 story 的对话**下限**语义")
    print("=" * 76)
    results.append(_case(
        "混入 story 的 dialogue_ratio（下限）",
        lambda s: s.replace(
            "        name=\"dialogue_ceiling\", metric=\"dialogue_ratio\", direction=\"le\",",
            "        name=\"dialogue_ceiling\", metric=\"dialogue_ratio\", direction=\"ge\",",
            1),
    ))

    print()
    print("=" * 76)
    print("变异 4：检测直出术语时不剥掉世界内说法（自我误报）")
    print("=" * 76)
    results.append(_case(
        "不剥掉说法",
        lambda s: s.replace(
            "    for term, replacement in mappings:\n"
            "        if replacement and replacement in text:\n"
            "            text = text.replace(replacement, \"\\u0000\")",
            "    pass  # 变异：不剥掉说法", 1),
    ))

    print()
    print("=" * 76)
    print("变异 5：命中率把未提到的知识点计入分母（分母造假）")
    print("=" * 76)
    results.append(_case(
        "未提到也计入分母",
        lambda s: s.replace(
            "        if used_ok:\n"
            "            hit += 1\n"
            "            expected += 1\n"
            "        elif used_bare:\n"
            "            expected += 1          # 直出 = 明确没做对",
            "        expected += 1   # 变异：无条件计入分母\n"
            "        if used_ok:\n"
            "            hit += 1", 1),
    ))

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, _ = _run()
    print(f"  {'★ 仍在失败（还原失败）' if ok else '全部测试通过 ✓'}")

    print()
    print("=" * 76)
    passed = sum(results)
    all_ok = passed == len(results) and not ok
    print(f"  结论：{passed}/{len(results)} 个变异被拦下"
          f"{'，且还原后通过 ✓' if all_ok else '　★ 不通过'}")
    print("=" * 76)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
