"""缺陷 2 修法 C 上线后的**真伪判定** —— 报警章到底是硬切，还是模型的旗没置对？

## 现在有真数据了

`debug/plans_failed_00001-00005.json` 落下了失败批次的**当轮产物**
（这正是本轮新加的诊断通道）。

报警：
    第2章 陆沉的独立办公室 → 市局刑侦支队第一会议室   （b_is_transition=false）
    第5章 复核室           → 陆沉独立办公室/韩明办公室（b_is_transition=false）

要回答：这两条**该不该**报警？
  · 若该报警 → 模型确实漏置旗（prompt 已明确"同楼换房间也算"）→ 真缺陷
  · 若不该   → 我们的判据仍有问题 → 继续修判据
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.graph.nodes.detail_planner import plan_from_dict  # noqa: E402

REF = pathlib.Path("data/artifacts/books/16/debug/plans_failed_00001-00005.json")
raw = json.loads(REF.read_text(encoding="utf-8"))
plans = {int(k): plan_from_dict(v) for k, v in raw.items()}

print("=" * 84)
print("逐个报警章的真实内容")
print("=" * 84)
for no in (2, 5):
    p = plans.get(no)
    if p is None:
        print(f"\n第{no}章：不在 debug 产物里")
        continue
    prev = plans.get(no - 1)
    print(f"\n第 {no} 章《{p.title}》")
    print(f"  is_transition = {p.is_transition}")
    print(f"  beat_type     = {p.beat_type}")
    print(f"  entry 地点    = {p.entry_state.location_name!r}")
    if prev:
        print(f"  上一章 exit 地点 = {prev.exit_state.location_name!r}")
    print(f"  场景拍点：")
    for i, s in enumerate(p.scenes, 1):
        mark = "  ← 首拍" if i == 1 else ""
        print(f"    [{i}] seq={s.seq} 地点字段={'无（SceneBeat 没有该字段）'}{mark}")
        print(f"         goal={s.goal[:66]!r}")
    print(f"  首拍 why_now  = {p.scenes[0].why_now[:90]!r}")
    if len(p.scenes) > 1:
        print(f"  次拍 why_now  = {p.scenes[1].why_now[:90]!r}")
    print(f"  plot          = {p.plot[:90]!r}")

print()
print("=" * 84)
print("全批 5 章的 transition 分布（看模型整体行为）")
print("=" * 84)
for no in sorted(plans):
    p = plans[no]
    a = p.entry_state.location_name
    b = p.exit_state.location_name
    print(f"  第{no}章 trans={str(p.is_transition):<5} beat={str(p.beat_type.value if hasattr(p.beat_type,'value') else p.beat_type):<14} "
          f"{a[:22]!r} → {b[:22]!r}")

print()
n_trans = sum(1 for p in plans.values() if p.is_transition)
print(f"  置旗率：{n_trans}/{len(plans)} = {n_trans / len(plans):.0%}")
print()
print("=" * 84)
print("判定")
print("=" * 84)
print(
    "  关键看：报警章的场景拍点里**是否真的换了地方**。\n"
    "  · 若场景地点前后一致（只是 entry/exit 措辞不同）→ 判据仍偏严\n"
    "  · 若场景确实换了地方而 is_transition=false → 模型漏置旗（真缺陷）\n"
)
