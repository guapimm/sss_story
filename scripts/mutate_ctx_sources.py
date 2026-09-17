"""变异测试：证明 0-4 的注册表**真的**解开了原来的阻塞。

原缺陷：`CONTEXT_SOURCES` 是封闭元组，`_check_coverage` 对表外键
`raise ValueError` → learn 的归因键一上报就**运行期崩**。

本脚本复现"learn 第一次上报归因"这个场景，分别在
「修复前」与「修复后」两种语义下跑，证明差别真实存在。

⚠️ 变异手法：直接把 `_check_coverage` 的判据换回**只用内置元组**
（模拟修复前的行为），确认测试会失败。这是**真变异** ——
改的是被保护机制真正读取的那一行（项目已吃过一次假变异的亏，
见 scripts/mutate_rule03.py 的说明）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # scripts/ -> 仓库根
TARGET = ROOT / "src/storyforge/observability/metering.py"

GOOD = "        known = set(registered_context_sources())\n"
BAD = "        known = set(CONTEXT_SOURCES)\n"   # 修复前的行为


def _run_registry_tests() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_metering.py",
         "-q", "-k", "register or unregistered or context_sources"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-700:]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    assert GOOD in src, "找不到目标行（_check_coverage 可能已被改过）"

    print("=" * 76)
    print("变异：把 `_check_coverage` 的判据换回封闭元组（修复前行为）")
    print("=" * 76)
    TARGET.write_text(src.replace(GOOD, BAD), encoding="utf-8")
    caught, out = _run_registry_tests()
    TARGET.write_text(src, encoding="utf-8")   # 立即还原
    print(f"  测试{'失败 ✓（正确：说明注册表确实是必需的）' if caught else '★ 仍全绿（注册表形同虚设）'}")
    if caught:
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("E ") or "failed" in s or "passed" in s:
                print(f"    {s}")
    else:
        print(out)

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, out2 = _run_registry_tests()
    print(f"  {'★ 仍在失败（还原失败）' if ok else '通过 ✓'}")
    if ok:
        print(out2)
    return 0 if (caught and not ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
