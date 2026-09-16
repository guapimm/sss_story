"""D-16 阈值判定：把基线的失败标签**按阶段**重新归因。

## 关键洞察

`multibook_e2e.py` 是**遇错即返回**的（`return r`），因此：

  · `volume_ok = False` → 标签只可能来自 `gate_issues`（台阶侧）
  · `volume_ok = True` 且 `skeleton_ok = False` → 标签只可能来自骨架 `violations`

基线里书 13/15 是 **`volume_ok: True` + `skeleton_ok: False`**，
却被标成 `volume:new_locations` —— 说明标签来自**骨架**消息
「骨架的故事线/段落未提及本卷新地图」（旧 `_classify` 见子串「新地图」就先命中）。

→ 这两本的真实类别是 `skeleton:new_locations`，**不是**台阶缺陷。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")

from multibook_e2e import _classify  # noqa: E402

base = json.loads(
    pathlib.Path("doc/research/multibook-e2e.json").read_text(encoding="utf-8")
)

#: 骨架侧两条违例消息的**真实措辞**（取自 `skeleton_planner.validate_skeleton`）
SKEL_MSGS = {
    "未提及本卷新地图": (
        "骨架的故事线/段落未提及本卷新地图：['X'] —— "
        "新地图必须在骨架里有对应的线，否则细纲不会安排换场"
    ),
    "未落实新势力": (
        "骨架未落实本卷台阶指定的新势力：['Y'] —— "
        "台阶规定了扩张，骨架必须落地，否则又回到「全书困在一处」"
    ),
}

print("=" * 78)
print("① 基线逐书阶段状态（`volume_ok` / `skeleton_ok`）")
print("=" * 78)
skeleton_labeled: list[int] = []
volume_labeled: list[int] = []

for r in base["results"]:
    bid = r["book_id"]
    v_ok, s_ok = r.get("volume_ok"), r.get("skeleton_ok")
    stage = "?"
    if not v_ok:
        stage = "台阶（未过闸门，遇错即返回）"
        volume_labeled.append(bid)
    elif not s_ok:
        stage = "骨架（台阶已过 → 标签只可能来自骨架）"
        skeleton_labeled.append(bid)
    print(f"  书 {bid:<3} {r['genre']:<9} volume_ok={v_ok!s:<5} "
          f"skeleton_ok={s_ok!s:<5} → 实际失败阶段：{stage}")
    print(f"        基线标签：{r.get('failures')}")

print()
print("=" * 78)
print("② 骨架侧消息用**修好的** `_classify` 会归到哪一类")
print("=" * 78)
for name, msg in SKEL_MSGS.items():
    print(f"  {name:<14} → {_classify(msg)}")

print()
print("=" * 78)
print("③ D-16 阈值判定")
print("=" * 78)
print(f"  台阶侧（volume_ok=False）    ：{volume_labeled}")
print(f"  骨架侧（volume_ok=True, skeleton_ok=False）：{skeleton_labeled}")
print()
print("  基线只在**台阶**阶段记录过 `volume:new_locations` 吗？→ 否。")
print("  书 13/15 台阶**已通过**（volume_ok=True），"
      "其 `volume:new_locations` 标签来自骨架消息。")
print()
n = len(skeleton_labeled)
print(f"  → `skeleton:new_locations` 实际涉及 **{n} 本**（{skeleton_labeled}）")
print("  → 加上本轮书 16（同类，已取证）="
      f" **{n + 1} 本 ≥ 3**")
print()
if n + 1 >= 3:
    print("  ★ 按 D-16「≥3 本失败 = 通用缺陷必修」→ **这一类必须修**。")
    print("    且已查明根因**不是匹配器**（书 16 取证：该地点在圣经里早已确立，")
    print("    骨架把它归为势力是正确的；错在台阶把它标成了 new_location）。")
else:
    print("  → 仍属单书特例，只记录。")
