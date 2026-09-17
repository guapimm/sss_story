"""变异测试：证明 0-7 的端到端测试**真的**在测接线。

`health_beats_injected > 0` 是本项目最容易"假绿"的断言之一 ——
它可能是 0 而测试仍通过（若测试写的是 `>= 0`），
也可能是测试根本没走到注入路径。

本脚本做**两次真变异**，每次移除一个断点：

  变异 1：把 `build_detail_batch(health_schedule=...)` 参数删掉
          → 模拟修复前（books.py:303 没传）→ 测试必须失败
  变异 2：让 `build_health_schedule` 直接返回 None
          → 模拟"没落库/读不到伤势"→ 测试必须失败

两次都失败 ⇒ 断言确实跨过了**两个**断点。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/storyforge/graph/graphs/books.py"

TEST_ID = "tests/unit/test_health_wiring.py::test_detail_graph_injects_health_beats_from_db"
#: 第三个断点的测试（heal_scene_planned 写回）
TEST_ID_WRITEBACK = (
    "tests/unit/test_health_wiring.py::"
    "test_detail_graph_writes_back_heal_scene_planned"
)

#: 变异 1 的目标：那一行注入参数
PASS_SCHEDULE = "            health_schedule=health_schedule,      # ★ 0-7：此前缺失\n"
#: 变异 2 的目标：拿伤势的那一步
LOAD_INJURIES = "        injuries = repo.list_active(book_id)\n"
#: 变异 3 的目标：写回 heal_scene_planned 的那一步
WRITE_BACK = "        _mark_heal_planned(deps, health_schedule, log_book_id=book_id)\n"


def _run(test_id: str = TEST_ID) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-800:]


def _report(name: str, caught: bool, out: str) -> bool:
    print(f"  {name}：{'拦下 ✓' if caught else '★ 没拦下（测试没测到这一环）'}")
    if not caught:
        print(out)
    return caught


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    assert PASS_SCHEDULE in src, "找不到 health_schedule= 注入行"
    assert LOAD_INJURIES in src, "找不到 list_active 调用行"
    results: list[bool] = []

    # ---- 变异 1：不传 schedule（模拟修复前） ----
    print("=" * 76)
    print("变异 1：删掉 `health_schedule=health_schedule`（模拟 books.py 修复前）")
    print("=" * 76)
    TARGET.write_text(src.replace(PASS_SCHEDULE, ""), encoding="utf-8")
    caught, out = _run()
    TARGET.write_text(src, encoding="utf-8")
    results.append(_report("参数未传", caught, out))

    # ---- 变异 2：读不到伤势（模拟 repositories 为空） ----
    print()
    print("=" * 76)
    print("变异 2：让 `list_active` 走异常分支 → schedule 为 None（模拟没落库）")
    print("=" * 76)
    bad = src.replace(
        LOAD_INJURIES,
        "        raise RuntimeError('变异：模拟读不到伤势')\n" + LOAD_INJURIES,
    )
    assert bad != src
    TARGET.write_text(bad, encoding="utf-8")
    caught2, out2 = _run()
    TARGET.write_text(src, encoding="utf-8")
    results.append(_report("读不到伤势", caught2, out2))

    # ---- 变异 3：不写回 heal_scene_planned ----
    print()
    print("=" * 76)
    print("变异 3：删掉 `_mark_heal_planned(...)`（模拟恢复链路最后一环缺失）")
    print("=" * 76)
    assert WRITE_BACK in src, "找不到 _mark_heal_planned 调用行"
    TARGET.write_text(src.replace(WRITE_BACK, ""), encoding="utf-8")
    caught3, out3 = _run(TEST_ID_WRITEBACK)
    TARGET.write_text(src, encoding="utf-8")
    results.append(_report("未写回 heal_scene_planned", caught3, out3))

    # ---- 还原复验 ----
    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, out4 = _run()
    print(f"  端到端测试 {'★ 仍失败（还原失败）' if ok else '通过 ✓'}")
    if ok:
        print(out4)

    print()
    print("=" * 76)
    for name, c in zip(("参数未传", "读不到伤势", "未写回 heal_scene_planned"), results):
        print(f"  {'✓' if c else '★'} {name} 被拦下")
    print(f"  {'✓' if not ok else '★'} 还原后复验通过")
    print()
    print("  ⇒ 三处断点（③落库 / ④接线 / ⑤写回）都有测试独立覆盖。")
    return 0 if (all(results) and not ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
