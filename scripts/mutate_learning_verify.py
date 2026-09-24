"""变异测试：证明「核验」不是摆设（删掉它、放宽它，测试必须红）。

## 为什么核验节点特别需要变异测试

核验的最大失败模式**不是"判错"，而是"恒真"**：
如果它永远返回 `supported`，或者准确率永远算成 1.0，
那么它在界面上"绿灯通过"，而实际什么都没查出来。

★ 本项目已记录：**一个"抓不到问题"的检查等于没有**
（规则 3 四类漏检、规则 22 曾完全失效、`validate_map` 只查"有没有"
不查"对不对"导致"溯源 100%"是假绿）。

故本脚本的每个变异都模拟一种**真实的退化方式**。

## 五个变异

  变异 1：`accuracy()` 把空结果算成 1.0
          → 复现"空输出 = 全通过"的假绿
  变异 2：未知判定被塞成 `supported`
          → 复现"判不了就当成通过"
  变异 3：`consecutive_unsupported()` 退化为数总数
          → 复现"分散幻觉与集中幻觉不再区分"（丢掉硬门禁的区分力）
  变异 4：`unsupported` 也计入通过
          → 复现"核验放过幻觉"（核验的核心职责失效）
  变异 5：核验节点与生成节点同档（`tier` premium → balanced）
          → 复现"自己批自己"（方案 B1 的全部要点被抹掉）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "src/storyforge/infra/llm/learning_schemas.py"
TOML = ROOT / "config/providers.toml"

TEST_ID = "tests/unit/test_learning_verify.py"


def _run() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_ID, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-600:]


def _case(title: str, path: Path, mutate) -> bool:
    original = path.read_text(encoding="utf-8")
    mutated = mutate(original)
    assert mutated != original, f"变异「{title}」没有改动文件"
    path.write_text(mutated, encoding="utf-8")
    caught, out = _run()
    path.write_text(original, encoding="utf-8")      # 立即还原
    assert path.read_text(encoding="utf-8") == original, "还原失败！"

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
    print("变异 1：空结果的准确率算成 1.0（假绿）")
    print("=" * 76)
    results.append(_case(
        "空结果 accuracy = 1.0",
        SCHEMAS,
        lambda s: s.replace(
            "        if total == 0:\n            return 0.0",
            "        if total == 0:\n            return 1.0", 1),
    ))

    print()
    print("=" * 76)
    print("变异 2：未知判定被塞成 supported（判不了就放过）")
    print("=" * 76)
    results.append(_case(
        "未知判定伪装成通过",
        SCHEMAS,
        lambda s: s.replace(
            '        return aliases.get(s, s)',
            '        return aliases.get(s, "supported")', 1),
    ))

    print()
    print("=" * 76)
    print("变异 3：连续 unsupported 退化为数总数（丢区分力）")
    print("=" * 76)
    results.append(_case(
        "连续 ≠ 总数",
        SCHEMAS,
        lambda s: s.replace(
            "        best = cur = 0\n"
            "        for v in self.verdicts:\n"
            "            cur = cur + 1 if v.verdict == \"unsupported\" else 0\n"
            "            best = max(best, cur)\n"
            "        return best",
            "        return sum(1 for v in self.verdicts if v.verdict == \"unsupported\")",
            1),
    ))

    print()
    print("=" * 76)
    print("变异 4：unsupported 也计入通过（核验放过幻觉）")
    print("=" * 76)
    results.append(_case(
        "unsupported 计入通过",
        SCHEMAS,
        lambda s: s.replace(
            '        return (c["supported"] + c["inferred"]) / total',
            '        return (c["supported"] + c["inferred"] + c["unsupported"]) / total', 1),
    ))

    print()
    print("=" * 76)
    print("变异 5：核验与生成同档（自己批自己）")
    print("=" * 76)
    results.append(_case(
        "核验节点降回 balanced 档",
        TOML,
        lambda s: s.replace(
            "[nodes.learning_verify]\ntier = \"premium\"",
            "[nodes.learning_verify]\ntier = \"balanced\"", 1),
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
