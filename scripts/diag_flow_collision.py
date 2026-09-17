"""盘点：learn 的 25 流派 vs story 的通用流派 —— **ID 是否撞名**。

## 为什么必须先查这个

两者在旧项目里是**两套独立数据**：
  · `flows.py`        → learn 的 25 个修仙流派（本阶段要移植的）
  · `story_flows.py`  → story 的 16 个通用流派

如果 ID 撞名，`config/flows/` 下会出现"同一 id 两套语义"——
而 `xianxia.toml` 的 `flows` 字段同时引用两边的 id（实测有交集），
撞名会让"选流派"这一步产生歧义。

用法：`uv run python scripts/diag_flow_collision.py`
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"E:\AI_project\story_llm\backend")

from app.flows import FLOWS as LEARN  # type: ignore[import-not-found]  # noqa: E402
from app.story_flows import STORY_FLOWS as STORY  # type: ignore[import-not-found]  # noqa: E402
from app.story_genres import GENRES  # type: ignore[import-not-found]  # noqa: E402


def main() -> int:
    learn_ids = set(LEARN)
    story_ids = set(STORY)

    print("=" * 76)
    print("规模")
    print("=" * 76)
    print(f"  learn 流派（flows.py）        {len(learn_ids)}")
    print(f"  story 通用流派（story_flows）  {len(story_ids)}")

    both = learn_ids & story_ids
    print()
    print("=" * 76)
    print(f"★ ID 撞名：{len(both)} 个")
    print("=" * 76)
    for i in sorted(both):
        lv, sv = LEARN[i], STORY[i]
        s_desc = str(sv.get("hook") or sv.get("desc") or sv.get("structure") or "")
        print(f"\n  【{i}】")
        print(f"     learn: {lv['name']}")
        print(f"            hook = {lv['hook'][:60]}")
        print(f"     story: {sv.get('name')}")
        print(f"            {s_desc[:60]}")

    print()
    print("=" * 76)
    print("xianxia 题材引用的 flows")
    print("=" * 76)
    g = next(x for x in GENRES if x["id"] == "xianxia")
    ref = list(g["flows"])
    print(f"  全部 {len(ref)} 个：{ref}")
    print(f"  属于 learn : {sorted(set(ref) & learn_ids)}")
    print(f"  属于 story : {sorted(set(ref) & story_ids)}")
    unknown = sorted(set(ref) - learn_ids - story_ids)
    print(f"  ★ 两边都不是：{unknown}")

    print()
    print("=" * 76)
    print("learn 25 流派的字段完整性")
    print("=" * 76)
    bad = []
    for k, v in LEARN.items():
        for f in ("name", "hook", "structure", "protagonist",
                  "narrative_devices", "arc_example", "scenes"):
            if not v.get(f):
                bad.append(f"{k}.{f}")
    print(f"  缺失字段：{bad or '（无）'}")
    print(f"  narrative_devices 均为 3 条："
          f"{all(len(v['narrative_devices']) == 3 for v in LEARN.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
