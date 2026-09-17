"""确定权威行数口径 —— 契约测试用的是哪种。

背景：同一文件出现三个不同行数（797 / 777 / 682），
必须搞清契约规则 10 的 800 行上限是按哪个口径判的，
否则「文件是否还有空间放 learn 代码」这个结论会反过来。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(r"E:\AI_project\story_commandcode")

FILES = [
    "src/storyforge/infra/llm/client.py",
    "src/storyforge/infra/llm/schemas.py",
    "src/storyforge/graph/state.py",
    "src/storyforge/cli.py",
]

print("=" * 84)
print("① 各口径的行数")
print("=" * 84)
print(f"{'文件':<46}{'bytes':>8}{'readlines':>11}{'split':>8}{'CRLF':>7}")
for rel in FILES:
    p = ROOT / rel
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    n_readlines = len(text.splitlines())          # splitlines: 任意行尾
    n_lf = text.count("\n")                        # \n 计数
    n_crlf = text.count("\r\n")
    print(f"{pathlib.Path(rel).name:<46}{len(raw):>8}"
          f"{n_readlines:>11}{n_lf:>8}{n_crlf:>7}")

print()
print("=" * 84)
print("② 契约测试怎么数的（读它自己的实现）")
print("=" * 84)
gate = (ROOT / "tests/contract/test_architecture_invariants.py").read_text("utf-8")
for i, line in enumerate(gate.splitlines(), 1):
    if "MAX_FILE_LINES" in line or "splitlines" in line or "read_text" in line:
        print(f"  L{i}: {line.strip()}")

print()
print("=" * 84)
print("③ 结论")
print("=" * 84)
print("  `read_text().splitlines()` 是 Python 的标准口径（能正确处理 CRLF 与 LF）。")
print("  PowerShell 的 `Get-Content | Measure-Object -Line` 在混用行尾时会数错，")
print("  以它为口径得到的行数**不可用于判断 800 行上限**。")
print()
print("  → 判断「文件还能不能放新代码」必须用 splitlines() 口径。")
