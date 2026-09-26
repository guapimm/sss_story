"""文件的**耦合诊断**：判断"该拆"还是"就这样"。

用户 2026-09-24 明确要求（写入 `doc/07` §2.1.1 + `AGENTS.md`）：
**拆分判据是耦合，不是行数**。2026-09-26 进一步**取消了单文件 800 行上限**，
所以本脚本不再用于"是否超限"，而是回答：

  · 这个文件是不是**内聚**的？（全部内容是否同一职责）
  · 若拆，能说清"降低了哪一处耦合"吗？
  · 各类内容的占比（代码 / 注释 / docstring / 空行）是多少

本脚本给出这些证据，供人判断。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/storyforge/infra/llm/client.py"

#: 各顶层类"属于哪个职责"。全部同一职责 = 内聚。
EXPECTED_ROLE = "LLM 客户端 + 其上下文/结果/错误/配置类型"


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    n = src.count("\n") + 1
    tree = ast.parse(src)

    print("=" * 74)
    print(f"{TARGET.relative_to(ROOT)}  —— {n} 行（契约上限 800）")
    print("=" * 74)

    classes = [x for x in tree.body if isinstance(x, ast.ClassDef)]
    funcs = [x for x in tree.body
             if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]
    print(f"\n顶层：{len(classes)} 个类 / {len(funcs)} 个函数")
    print(f"\n所有顶层类（判断是否内聚）：")
    for c in classes:
        span = (c.end_lineno or c.lineno) - c.lineno + 1
        print(f"  行{c.lineno:>4} {span:>4}行  {c.name}")
    print(f"\n→ 预期职责：{EXPECTED_ROLE}")

    print("\n" + "=" * 74)
    print("超限归因：注释 vs 代码")
    print("=" * 74)
    lines = src.splitlines()
    # 粗略统计：空行、纯注释行、含 docstring 的行
    blank = sum(1 for l in lines if not l.strip())
    comment = sum(1 for l in lines if l.strip().startswith("#"))
    # docstring 行数（用 AST 精确取）
    doc_lines = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                            ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                d = body[0]
                doc_lines += (d.end_lineno or d.lineno) - d.lineno + 1
    code = n - blank - comment - doc_lines
    print(f"  总行数     {n}")
    print(f"  空行       {blank}")
    print(f"  纯注释行   {comment}")
    print(f"  docstring  {doc_lines}")
    print(f"  → 实际代码 约 {code} 行")
    pct = 100 * (blank + comment + doc_lines) / n
    print(f"\n  非代码行占比 {pct:.0f}%")

    print("\n" + "=" * 74)
    print("判读")
    print("=" * 74)
    print("  · 若顶层类**全部同一职责** → 文件是内聚的，按新判据**不该拆**")
    print("  · 若超限主要由**注释/docstring**造成 → 正确做法是**精简注释**，")
    print("    而不是拆文件（拆了行数也不会降，只是把注释挪走）")
    print("  · 只有存在**互不相关的职责**时，才谈得上「拆完降低了哪处耦合」")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
