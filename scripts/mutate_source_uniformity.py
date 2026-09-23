"""变异测试：证明「来源全同」这条校验**真的会拦下问题**。

## 为什么必须有这个脚本

项目教训（规则 22，交接文档 §5）：**判定逻辑复杂的规则，它自己就是被测对象**。
只靠"跑一遍现有 `src/` 没报错"来确认，则规则**退化成永远放行**也照样绿灯。

规则 22 就是这么栽的：手写 AST 作用域分析有两个互相掩盖的 bug，
`pytest` 全绿而它抓不到要抓的缺陷。

## 本次要防的退化

2026-09-23 新增「全部知识点来源完全相同 → 报警」。若有人：
  · 把 `len(distinct) == 1` 写成 `!= 1`（判据反转）
  · 或删掉整段（回到修复前的假绿）
都不该逃过测试。本脚本用**文本级变异**验证这一点。

## 变异手法（与 `mutate_persist.py` 同风格）

直接改 `domain/learning.py` 的源码文本，跑测试，看是否**必须变红**。
每个变异跑完立即还原。

用法：
    uv run python scripts/mutate_source_uniformity.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "storyforge" / "domain" / "learning.py"
BACKUP = ROOT / "src" / "storyforge" / "domain" / "learning.py.mutbak"

TEST = "tests/unit/test_learning_map.py"

#: 要破坏的片段 → 破坏成什么（每条都必须让测试变红）
MUTATIONS: list[tuple[str, str, str]] = [
    (
        "判据反转（== 1 改成 != 1）",
        "if len(distinct) == 1:",
        "if len(distinct) != 1:",
    ),
    (
        "阈值放宽（>= 3 改成 >= 999，等于永不触发）",
        "if len(sourced) >= 3:",
        "if len(sourced) >= 999:",
    ),
    (
        "判据失效（distinct 恒为单元素）",
        "distinct = {tuple(sorted(p.source_urls)) for p in sourced}",
        "distinct = {()}",
    ),
]


def _run_tests() -> tuple[bool, str]:
    """返回 (是否全绿, 摘要)。"""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "--no-header", "-x"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    last = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["(无输出)"]
    return r.returncode == 0, last[0][:110]


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    BACKUP.write_text(original, encoding="utf-8")

    print("=" * 78)
    print("变异测试：来源「全同」校验")
    print("=" * 78)

    # 基线：不改任何东西，应全绿
    ok, summary = _run_tests()
    print(f"\n[基线] 未变异          → {'✓ 全绿' if ok else '★ 本来就红'}  {summary}")
    if not ok:
        print("★ 基线不绿，后续变异无意义 —— 先修测试")
        BACKUP.unlink(missing_ok=True)
        return 1

    caught = 0
    try:
        for name, old, new in MUTATIONS:
            if old not in original:
                print(f"\n[{name}] ★ 找不到目标片段，跳过")
                print(f"        期望片段：{old}")
                continue
            TARGET.write_text(original.replace(old, new, 1), encoding="utf-8")
            ok, summary = _run_tests()
            if ok:
                print(f"\n[{name}] ★★ 未拦下（测试全绿 = 校验无效）  {summary}")
            else:
                caught += 1
                print(f"\n[{name}] ✓ 已拦下  {summary}")
            TARGET.write_text(original, encoding="utf-8")
    finally:
        TARGET.write_text(original, encoding="utf-8")
        BACKUP.unlink(missing_ok=True)

    print()
    print("=" * 78)
    print(f"结果：{caught}/{len(MUTATIONS)} 拦下")
    if caught == len(MUTATIONS):
        print("  ✓ 该校验**不是恒真断言** —— 判据真在看数据")
    else:
        print("  ★ 有变异未被拦下 ⇒ 校验可能退化成永远放行")
    print("=" * 78)
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
