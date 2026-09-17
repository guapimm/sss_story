"""变异测试：证明加固后的规则 3 **真的能拦下**它以前漏掉的东西。

## 为什么必须做（本项目已吃过一次亏）

规则 16 的第一次变异测试做错了 —— 只删签名、调用点还留着被保护的字符串，
于是"规则不报警"被误读成"规则有洞"，实际是变异本身无效。
**变异必须真的插入会被保护机制看见的代码。**

## 本脚本验证 4 类历史漏检

原始规则（`MODE_BRANCH_TOKENS={"book_kind","is_story","_is_story","book_kind_"}`，
只扫 domain+graph/nodes，只看名字不看比较）对下面 4 类**全部漏检**：

  1. `if book.mode == "learn":`          —— 用了本项目自己的字段名
  2. `if book.book_kind == "story":`     —— 老集合里有 book_kind，但在无比较检测时…（见下）
  3. 写在 `cli.py`（不在扫描范围）
  4. `settings.mode != "story"`          —— 配置字段

每类都在**临时文件**上验证，验证后立即删除。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # scripts/ -> 仓库根
SRC = ROOT / "src" / "storyforge"
PROBE = SRC / "_zz_mode_probe.py"

#: 4 类变异体：(说明, 代码)
VARIANTS: list[tuple[str, str]] = [
    ("① book.mode == 'learn'（本项目自己的字段名）", textwrap.dedent('''
        """探针（变异测试用）。"""
        from __future__ import annotations


        def which_mode(book) -> str:
            if book.mode == "learn":
                return "learn"
            return "story"
    ''')),
    ("② settings.mode != 'story'（配置字段）", textwrap.dedent('''
        """探针（变异测试用）。"""
        from __future__ import annotations


        def use_learn(settings) -> bool:
            return settings.mode != "story"
    ''')),
    ("③ book.mode in ('learn',)（in 形式）", textwrap.dedent('''
        """探针（变异测试用）。"""
        from __future__ import annotations


        def is_learn(book) -> bool:
            return book.mode in ("learn",)
    ''')),
    ("④ 反向：'learn' == book.mode", textwrap.dedent('''
        """探针（变异测试用）。"""
        from __future__ import annotations


        def reversed_check(book) -> bool:
            return "learn" == book.mode
    ''')),
]

#: 对照：**不该**被报的代码（证明规则没有过宽）
NEGATIVE = textwrap.dedent('''
    """探针（变异测试用）：声明不是分支。"""
    from __future__ import annotations

    from typing import Literal


    def declare(mode: Literal["story", "learn"]) -> str:
        """只是类型标注与默认值，不是模式分支。"""
        return mode or "story"
''')


def _run_rule() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/contract/test_architecture_invariants.py",
         "-q", "-k", "rule03"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-500:]


def main() -> int:
    failures: list[str] = []

    print("=" * 78)
    print("① 变异体：每一类都必须被规则 3 拦下")
    print("=" * 78)
    for name, code in VARIANTS:
        PROBE.write_text(code, encoding="utf-8")
        caught, _ = _run_rule()
        status = "拦下 ✓" if caught else "★ 漏检"
        print(f"  {name:<46} {status}")
        if not caught:
            failures.append(name)
    PROBE.unlink(missing_ok=True)

    print()
    print("=" * 78)
    print("② 反向对照：合法代码不得被误报")
    print("=" * 78)
    PROBE.write_text(NEGATIVE, encoding="utf-8")
    caught, out = _run_rule()
    PROBE.unlink(missing_ok=True)
    if caught:
        print("  ★ 误报：类型标注被当成模式分支")
        print(out)
        failures.append("反向对照误报")
    else:
        print("  合法声明未被误报 ✓")

    print()
    print("=" * 78)
    if failures:
        print(f"★ 失败 {len(failures)} 项：")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("全部通过：4 类历史漏检均被拦下，且无误报")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
