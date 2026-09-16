"""收集**全部真实**的相邻地点对（成功+失败），为连续性容错匹配定口径。

## 为什么必须基于真实数据（D-16）

缺陷 2 已修过一轮（统一 `is_transition` 定义），但 6 书回归显示
`detail:continuity` 命中 **4 本**（12/13/15/16）—— 达 D-16「必修」阈值。

而逐条看，其中至少两条是**明显假阳性**：

    书15  铁门关驿丞署·院内      → 铁门关驿丞署·前堂      （同一驿站，不同房间）
    书16  市公安局地下特殊物证库入口走廊 → 市局地下特殊物证库入口走廊（市公安局 = 市局，纯缩写）

说明**「地点是否真的变了」这个测量本身**仍不可靠。
本脚本把所有真实相邻对（含未报警的）捞出来，用于：
  · 设计容错匹配的口径
  · 事后验证"修完是否既消假阳性、又不制造假阴性"
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.config import get_settings  # noqa: E402
from storyforge.graph.nodes.detail_planner import plan_from_dict  # noqa: E402
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402

store = ArtifactStore.from_settings(get_settings())
BOOKS = (11, 12, 13, 14, 15, 16)

all_pairs: list[tuple[int, int, int, str, str, bool]] = []

for bid in BOOKS:
    d = pathlib.Path(f"doc/research/_diag_detail_{bid}.json")
    if not d.exists():
        continue
    diag = json.loads(d.read_text(encoding="utf-8"))
    ref = diag.get("last_plans_ref") or ""

    # 合并：已落盘的成功章节 + 失败批次的 debug 产物
    plans: dict[int, object] = {}
    for no in range(1, (diag.get("expected") or 0) + 1):
        try:
            from storyforge.graph.nodes.detail_planner import load_plan  # noqa: PLC0415
            plans[no] = load_plan(store, bid, no)
        except Exception:  # noqa: BLE001
            pass
    if ref:
        try:
            raw = json.loads(store.read_text(ref))
            for k, v in raw.items():
                plans.setdefault(int(k), plan_from_dict(v))
        except Exception:  # noqa: BLE001
            pass

    for no in sorted(plans):
        a, b = plans.get(no), plans.get(no + 1)
        if a is None or b is None:
            continue
        la = (a.exit_state.location_name or "").strip()
        lb = (b.entry_state.location_name or "").strip()
        if la and lb:
            all_pairs.append((bid, no, no + 1, la, lb, b.is_transition))

print("=" * 100)
print(f"全部真实相邻地点对：{len(all_pairs)} 对（跨 {len({p[0] for p in all_pairs})} 本书）")
print("=" * 100)

flagged = [p for p in all_pairs if p[3] != p[4] and not p[5]]
passed = [p for p in all_pairs if p[3] == p[4] or p[5]]

print(f"\n【报警的（地点名不同 且 未声明换场）】{len(flagged)} 对")
for bid, a, b, la, lb, _ in flagged:
    print(f"  书{bid:>2} 第{a}→{b}章")
    print(f"        {la}")
    print(f"     →  {lb}")

print(f"\n【放过的】{len(passed)} 对")
for bid, a, b, la, lb, tr in passed[:40]:
    same = "同" if la == lb else "异"
    print(f"  书{bid:>2} 第{a}→{b}章 [{same}] trans={tr}")
    if la != lb:
        print(f"        {la}")
        print(f"     →  {lb}")

# 结果落盘供下一步设计使用
out = pathlib.Path("doc/research/_all_adjacent_pairs.json")
out.write_text(json.dumps([
    {"book": p[0], "a": p[1], "b": p[2], "exit": p[3], "entry": p[4],
     "is_transition": p[5]}
    for p in all_pairs
], ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n已落盘：{out}")
