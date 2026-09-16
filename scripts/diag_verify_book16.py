"""核对书 16 首次**全链路通过**后的细纲产物（15 章）。

这是本轮第一次跑通「圣经 → 台阶 → 骨架 → 细纲」，
必须确认产物**是真的**，而不是"判定通过但内容空"。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.graph.nodes.detail_planner import plan_from_dict  # noqa: E402

D = pathlib.Path("data/artifacts/books/16/plans")
files = sorted(D.glob("*.json"))
print("=" * 88)
print(f"细纲产物：{len(files)} 个文件")
print("=" * 88)
print(f"{'章':<4}{'标题':<24}{'节拍':<14}{'换场':<6}{'场景':<5}{'plot字数':<9}")
total_scenes = 0
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    p = plan_from_dict(d)
    total_scenes += len(p.scenes)
    print(f"{p.global_no:<4}{p.title[:22]:<24}"
          f"{str(getattr(p.beat_type, 'value', p.beat_type)):<14}"
          f"{str(p.is_transition):<6}{len(p.scenes):<5}{len(p.plot):<9}")

print()
print("=" * 88)
print("场景级地点（本轮刚修复的数据丢失项 —— 现在应当有值）")
print("=" * 88)
with_loc = 0
total = 0
for f in files:
    p = plan_from_dict(json.loads(f.read_text(encoding="utf-8")))
    locs = [s.location_name for s in p.scenes]
    total += len(locs)
    with_loc += sum(1 for x in locs if x)
    print(f"  第{p.global_no:>2}章: {locs}")
print()
print(f"  场景地点填充率：{with_loc}/{total} = {with_loc / total:.0%}"
      if total else "  无场景")
print()
print("=" * 88)
print("章首/章末地点（连续性检查用的字段）")
print("=" * 88)
for f in files:
    p = plan_from_dict(json.loads(f.read_text(encoding="utf-8")))
    print(f"  第{p.global_no:>2}章  entry={p.entry_state.location_name[:26]!r}"
          f"  exit={p.exit_state.location_name[:26]!r}")
print()
print("=" * 88)
print("汇总")
print("=" * 88)
print(f"  章数      : {len(files)}")
print(f"  场景总数  : {total_scenes}")
print(f"  平均场景/章: {total_scenes / len(files):.1f}")
n_trans = sum(
    1 for f in files
    if plan_from_dict(json.loads(f.read_text(encoding="utf-8"))).is_transition
)
print(f"  换场章    : {n_trans}/{len(files)} = {n_trans / len(files):.0%}")
