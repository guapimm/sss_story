"""抽取旧项目**知识小说（learn）专属**流派库 `flows.py` 的完整清单。

用户要求「玄幻修仙的流派清单，根据已有的来」——
注意有两个流派库，**learn 用的是 `flows.py`（25 个修仙流派）**，
不是 `story_flows.py`（16 个通用流派）：

    flows.py:2          "纯小说专属流派库（与知识小说 app/flows.py 的 25 个修仙流派隔离）"
    story_flows.py:2    "纯小说专属流派库（与知识小说 app/flows.py 的 25 个修仙流派隔离）"

→ 以 `flows.py` 为准（它就是 learn 的流派库）。
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(r"E:\AI_project\story_llm\backend\app\flows.py")
tree = ast.parse(SRC.read_text(encoding="utf-8"))

flows_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "FLOWS":
                flows_node = node.value
if flows_node is None:
    raise SystemExit("找不到 FLOWS")

rows: list[dict] = []
for k, v in zip(flows_node.keys, flows_node.values):
    key = ast.literal_eval(k)
    if not isinstance(v, ast.Dict):
        continue
    d = {ast.literal_eval(kk): ast.literal_eval(vv)
         for kk, vv in zip(v.keys, v.values)}
    rows.append({"id": key, **d})

print("=" * 92)
print(f"★ 旧项目 learn（知识小说）流派库 flows.py —— 共 {len(rows)} 个")
print("=" * 92)
for i, r in enumerate(rows, 1):
    print(f"\n{i:>2}. {r['id']:<18} {r.get('name', '')}")
    print(f"    适用题材 : {r.get('scenes')}")
    print(f"    核心钩子 : {r.get('hook', '')[:76]}")
    print(f"    主角原型 : {r.get('protagonist', '')[:76]}")

print()
print("=" * 92)
print("★ 供确认用的紧凑清单（id → 中文名 → 钩子）")
print("=" * 92)
print(f"{'id':<20}{'中文名':<16}钩子")
for r in rows:
    print(f"{r['id']:<20}{r.get('name', ''):<16}{r.get('hook', '')[:52]}")

print()
print("=" * 92)
print("统计")
print("=" * 92)
scenes: dict[str, list[str]] = {}
for r in rows:
    for s in (r.get("scenes") or []):
        scenes.setdefault(s, []).append(r["id"])
print(f"  流派总数：{len(rows)}")
for s, ids in scenes.items():
    print(f"  题材 {s}: {len(ids)} 个流派")
dev = sum(len(r.get("narrative_devices") or []) for r in rows)
print(f"  叙事装置（narrative_devices）总数：{dev}")
print(f"  每张卡含：name / scenes / hook / structure / protagonist / "
      f"narrative_devices / arc_example")
