"""变异测试：证明 0-5 的映射完整性测试**真的**能拦下 `CHAPTER` 缺失。

原缺陷：`executor._stage_key` 只映射 4 个阶段，缺 `PipelineStage.CHAPTER`。
而 `orchestrator.py:220-222` 为**全部 5 个**枚举值建队列 →
派发 CHAPTER 单元必抛 `KeyError`。

本脚本做两次真变异（改的是被测试真正读取的表）：

  变异 1：从 `BUILTIN_STAGE_GRAPHS` 删掉 DETAIL
          → `test_stage_graph_map_covers_all_stages` 必须失败
  变异 2：把 `BUILTIN_STAGE_GRAPHS` 的值改成与 `build_all` 不一致
          → `test_builtin_stage_graph_names_match_build_all` 必须失败
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # scripts/ -> 仓库根
TARGET = ROOT / "src/storyforge/graph/graphs/books.py"

TESTS = [
    "tests/unit/test_graphs.py::test_stage_graph_map_covers_all_stages",
    "tests/unit/test_graphs.py::test_builtin_stage_graph_names_match_build_all",
]

DETAIL_LINE = '    PipelineStage.DETAIL: "detail",\n'


def _run(*test_ids: str) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *test_ids, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-700:]


def _restore(src: str) -> None:
    TARGET.write_text(src, encoding="utf-8")


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    assert DETAIL_LINE in src, "找不到 DETAIL 映射行"
    results: list[tuple[str, bool]] = []

    # ---- 变异 1：删掉一个内置阶段映射 ----
    print("=" * 76)
    print("变异 1：从 BUILTIN_STAGE_GRAPHS 删掉 DETAIL（模拟漏项）")
    print("=" * 76)
    TARGET.write_text(src.replace(DETAIL_LINE, ""), encoding="utf-8")
    caught, out = _run(TESTS[0])
    _restore(src)
    results.append(("覆盖完整性测试拦下漏项", caught))
    print(f"  {TESTS[0].split('::')[1]}："
          f"{'拦下 ✓' if caught else '★ 没拦下（测试失效）'}")
    if not caught:
        print(out)

    # ---- 变异 2：让映射值与 build_all 漂移 ----
    print()
    print("=" * 76)
    print("变异 2：把 SKELETON 的图名改成 'skeleton_typo'（模拟注册表漂移）")
    print("=" * 76)
    bad = src.replace(
        '    PipelineStage.SKELETON: "skeleton",\n',
        '    PipelineStage.SKELETON: "skeleton_typo",\n',
    )
    assert bad != src, "替换失败"
    TARGET.write_text(bad, encoding="utf-8")
    caught2, out2 = _run(TESTS[1])
    _restore(src)
    results.append(("注册表一致性测试拦下漂移", caught2))
    print(f"  {TESTS[1].split('::')[1]}："
          f"{'拦下 ✓' if caught2 else '★ 没拦下（测试失效）'}")
    if not caught2:
        print(out2)

    # ---- 还原复验 ----
    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, out3 = _run(*TESTS)
    print(f"  两条测试 {'★ 仍在失败（还原失败）' if ok else '通过 ✓'}")
    if ok:
        print(out3)

    print()
    print("=" * 76)
    all_ok = all(c for _, c in results) and not ok
    for name, c in results:
        print(f"  {'✓' if c else '★'} {name}")
    print(f"  {'✓' if not ok else '★'} 还原后复验通过")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
