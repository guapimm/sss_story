"""变异测试：证明规则 21 能拦下**真实的那个缺陷**（ProviderCapability）。

回归目标：`infra/llm/client.py` 曾写 `provider: ProviderCapability`，
而全仓从未定义该名字。本脚本把同一处改回去，规则 21 必须报错。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # scripts/ -> 仓库根
TARGET = ROOT / "src/storyforge/infra/llm/client.py"
GOOD = "        provider: ProviderSpec,\n"
BAD = "        provider: ProviderCapability,\n"


def _run_rule21() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/contract/test_architecture_invariants.py", "-q", "-k", "rule21"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-600:]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    assert GOOD in src, "找不到目标行（可能已被改过）"

    print("=" * 76)
    print("变异：把 `provider: ProviderSpec` 改回 `provider: ProviderCapability`")
    print("=" * 76)
    TARGET.write_text(src.replace(GOOD, BAD), encoding="utf-8")
    caught, out = _run_rule21()
    TARGET.write_text(src, encoding="utf-8")   # 立即还原
    print(f"  规则 21 {'拦下了 ✓（正确）' if caught else '★ 没拦下（规则失效）'}")
    if caught:
        for line in out.splitlines():
            if "注解名" in line or "未定义" in line:
                print(f"    {line.strip()}")
    else:
        print(out)

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, _ = _run_rule21()
    print(f"  规则 21 {'通过 ✓' if not ok else '★ 仍在报错（还原失败）'}")
    return 0 if (caught and not ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
