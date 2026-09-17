"""盘点两套 flow 数据的**完整对照** —— 为"放哪里 / 是否合并"提供依据。

## 背景

旧项目有**两套** flow 数据，字段不同、用途不同，但 **2 个 ID 撞名**：

    flows.py        → learn 的 25 个修仙流派（7 字段，含 scenes）
    story_flows.py  → story 的 16 个通用流派（8 字段，含 channels/universal）

而 `xianxia.toml` 的 `flows` 字段**同时引用两边的 id**：

    ['rebirth', 'system', 'underdog', 'invincible', 'sect', 'immortal_road']

用法：`uv run python scripts/diag_flow_datasets.py`
"""

from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, r"E:\AI_project\story_llm\backend")

from app.flows import FLOWS as LEARN  # type: ignore[import-not-found]  # noqa: E402
from app.story_flows import STORY_FLOWS as STORY  # type: ignore[import-not-found]  # noqa: E402


def _row(name: str, hook: str, width: int = 44) -> str:
    h = (hook or "").replace("\n", " ")
    return f"{name:<10} {h[:width]}"


def main() -> int:
    print("=" * 96)
    print("learn 25 个修仙流派（flows.py）")
    print("=" * 96)
    for i, (k, v) in enumerate(LEARN.items(), 1):
        print(f"{i:>2}. [{k:<22}] {_row(v['name'], v['hook'])}")

    print()
    print("=" * 96)
    print("story 16 个通用流派（story_flows.py）")
    print("=" * 96)
    for i, (k, v) in enumerate(STORY.items(), 1):
        ch = "/".join(v.get("channels") or [])
        uni = "通用" if v.get("universal") else "  "
        print(f"{i:>2}. [{k:<22}] {_row(str(v.get('name')), str(v.get('hook') or ''))}"
              f"  {uni} [{ch}]")

    print()
    print("=" * 96)
    print("★ 撞名的 2 个 —— 语义是否重复？")
    print("=" * 96)
    for k in sorted(set(LEARN) & set(STORY)):
        lv, sv = LEARN[k], STORY[k]
        print(f"\n【{k}】")
        print(f"  learn  name={lv['name']}")
        print(f"         hook={lv['hook']}")
        print(f"         structure={lv['structure'][:70]}")
        print(f"         protagonist={lv['protagonist'][:60]}")
        print(f"  story  name={sv.get('name')}")
        print(f"         hook={sv.get('hook')}")
        print(f"         structure={str(sv.get('structure'))[:70]}")
        print(f"         protagonist={str(sv.get('protagonist'))[:60]}")

    print()
    print("=" * 96)
    print("字段对照")
    print("=" * 96)
    lk = Counter()
    for v in LEARN.values():
        lk.update(v.keys())
    sk = Counter()
    for v in STORY.values():
        sk.update(v.keys())
    print(f"  learn 字段: {sorted(lk)}")
    print(f"  story 字段: {sorted(sk)}")
    print(f"  共有      : {sorted(set(lk) & set(sk))}")
    print(f"  仅 learn  : {sorted(set(lk) - set(sk))}")
    print(f"  仅 story  : {sorted(set(sk) - set(lk))}")

    print()
    print("=" * 96)
    print("story 16 流派里**非修仙**的（learn 只做玄幻修仙，用不到）")
    print("=" * 96)
    xianxia_related = {
        "rebirth", "system", "underdog", "invincible", "sect", "immortal_road",
        "mortal", "hidden_master", "sword_saint", "pill_master", "body_king",
        "artifact_forge", "array_master", "demon_path", "beast_tamer",
        "inheritance", "tribulation_crosser", "bloodline_awakening",
        "soul_cultivation", "spatial_master", "sect_leader", "weapon_spirit",
        "heavenly_dao", "wandering_sword", "merchant_cultivator",
        "dual_cultivation", "villain", "sect_business", "knowledge_seeker",
    }
    only_story = sorted(set(STORY) - set(LEARN))
    for k in only_story:
        v = STORY[k]
        tag = "修仙相关" if k in xianxia_related else "★ 非修仙"
        print(f"  [{k:<22}] {v.get('name')}  {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
