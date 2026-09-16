"""根因验证：骨架 schema **没有** places 字段，却在校验器里强制要求新地图"被提及"。

## 结构性不一致

| 台阶要求 | schema 里有对应字段吗 | 校验器怎么查 |
|---|---|---|
| `new_factions` | ✅ **有**（`SkeletonOut.factions`，且 prompt 明写"必须包含台阶指定的新势力"） | 查 **factions 字段** |
| `new_locations` | ❌ **没有**（`SkeletonOut` 只有 arcs/segments/characters/factions） | 只能**在 arcs+segments 的自由文本里子串搜索** |

后果：模型对两条要求的**可执行程度完全不同** ——

  · 势力：有字段 → 照抄进去即可 → 稳定通过
  · 地图：没字段 → 必须"把地名恰好写进某条线的描述或某段目标" →
    它写的是**这条线讲什么**（剧情），而不是**在哪儿发生**（地点）；
    地名是场景属性，不属于"故事线"的抽象层次

实测支持：书 16 的骨架把「警察局重案组」正确地放进了 `factions`
（有字段就填），但 arcs/segments 里没有它 —— **不是漏了，是没地方放**。

## 这解释了为什么这一类反复出现且修匹配器无效

匹配器已容错两次（LCS≥4 + 主名拆分），仍然失败 —— 因为问题不在匹配，
在于**要求的东西在 schema 里没有落点**。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.infra.llm.schemas import SkeletonOut  # noqa: E402

print("=" * 78)
print("① SkeletonOut 有哪些字段？")
print("=" * 78)
fields = list(SkeletonOut.model_fields)
for f in fields:
    print(f"   · {f}")
print()
print(f"   有 places / locations 字段吗？→ {'places' in fields or 'locations' in fields}")

print()
print("=" * 78)
print("② 两条台阶要求在 schema 里的落点对比")
print("=" * 78)
print(f"   new_factions → 字段 `factions`     存在: {'factions' in fields}")
print(f"   new_locations→ 字段 `locations`    存在: {'locations' in fields}")

print()
print("=" * 78)
print("③ 书 16 实测：势力落进了字段，地图无处可落")
print("=" * 78)
diag = json.loads(
    pathlib.Path("doc/research/_diag_skeleton_16.json").read_text(encoding="utf-8"))
sk = json.loads(diag["skeleton_excerpt"])
print(f"   factions 字段内容：{sk.get('factions')}")
print(f"   → 「警察局重案组」在 factions 里：{'警察局重案组' in (sk.get('factions') or [])}")
arc_txt = " ".join(a.get("description", "") + a.get("key", "") for a in sk.get("arcs", []))
seg_txt = " ".join(sk.get("segments", []))
print(f"   → 「警察局」在 arcs/segments：{'警察局' in arc_txt or '警察局' in seg_txt}")
print()
print("   ★ 结论：势力有字段 → 填进去了（通过）；")
print("            地图没字段 → 只能指望它恰好出现在剧情描述里 → 失败")

print()
print("=" * 78)
print("④ 为什么『再加容错』解决不了")
print("=" * 78)
print(
    "  匹配器已容错两次（LCS≥4 + 主名拆分）。书 16 复算：LCS=3，命中的是\n"
    "  「警察局」「重案组」这类**通用词** —— 真按 LCS 放宽到 3，会把\n"
    "  「某市公安局」与「另一个警察局」判成同一地点，制造**假阴性**。\n\n"
    "  问题不在匹配，在于**要求的东西在 schema 里没有落点**。\n"
    "  正确修法是给 SkeletonOut 加 `places`（或 `locations`）字段，\n"
    "  让『本卷用到哪些地点』成为**一等公民**，与 factions 对称。"
)
