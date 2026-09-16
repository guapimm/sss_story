"""缺陷 2 修法 C 的**上线后实测分析** —— 判"是假阳性还是真硬切"。

## 现在的数据（首次拿到真实相邻地点对）

`_diag_detail_16.json` 显示：细纲**真的产出了 10 章**，
12 个相邻对里只有 1 对报警。关键对照：

| 对 | 本章结束 | 下一章开始 | is_transition | 报警? |
|---|---|---|---|---|
| 5→6 | 老城区筒子楼外 | 海鸣市公安局办公室 | **false** | **否** |
| 11→12 | 老周家楼下 | 海鸣市公安局林深办公室 | ? | **是** |

两对形状**几乎相同**（都是"某地 → 公安局"），为什么一个报一个不报？

要回答的是：
  · 12 章是真的没声明换场（→ 真硬切，模型该改）？
  · 还是同样声明了但另有原因？
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.graph.nodes.detail_planner import load_plan  # noqa: E402
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402
from storyforge.config import get_settings  # noqa: E402

BID = 16
diag = json.loads(
    pathlib.Path(f"doc/research/_diag_detail_{BID}.json").read_text(encoding="utf-8"))

print("=" * 82)
print("① 诊断里的相邻对（只有 1 对报警）")
print("=" * 82)
for p in diag["adjacent_pairs"]:
    flag = "★报警" if (p["exit_location"] != p["entry_location"]
                     and not p["b_is_transition"]) else "  放过"
    short = f"{p['exit_location']} → {p['entry_location']}"
    print(f"  {p['a']:>2}→{p['b']:<3} {flag}  transition={str(p['b_is_transition']):<5} "
          f"{short[:52]}")
print(f"\n  transition 置旗率：{diag['transition_rate']:.0%}"
      f"（{sum(1 for p in diag['adjacent_pairs'] if p['b_is_transition'])}"
      f"/{len(diag['adjacent_pairs'])}）")

print()
print("=" * 82)
print("② 报警那条（第 12 章）的细纲原文")
print("=" * 82)
store = ArtifactStore.from_settings(get_settings())
try:
    p11 = load_plan(store, BID, 11)
    p12 = load_plan(store, BID, 12)
except Exception as exc:  # noqa: BLE001
    print(f"  读不到（第 12 章未落盘，失败批次不落盘是设计正确行为）：{exc}")
    print()
    print("  ⚠️ 这说明报警章**没有落盘** —— 只能从 diag 的相邻对推断。")
    print(f"  第 11 章 exit：{diag['adjacent_pairs'][-1]['exit_location']}"
          if diag["adjacent_pairs"] else "")
    raise SystemExit(0)

print(f"  第11章 exit : {p11.exit_state.location_name}")
print(f"  第12章 entry: {p12.entry_state.location_name}")
print(f"  第12章 is_transition = {p12.is_transition}")
print(f"  第12章 beat_type     = {p12.beat_type}")
print()
print("  第12章场景拍点：")
for i, s in enumerate(p12.scenes, 1):
    print(f"    [{i}] 地点={s.location_name!r}")
    print(f"        why_now={s.why_now[:70]!r}")
print()
print("  第11章末段拍点：")
for i, s in enumerate(p11.scenes[-2:], len(p11.scenes) - 1):
    print(f"    [{i}] 地点={s.location_name!r}")
