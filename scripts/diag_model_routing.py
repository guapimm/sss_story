"""验证：切换模型后，各节点实际路由到哪个模型（以及思考策略）。

## 为什么要有这个脚本

`providers.toml` 的 `route()` 取的是**同 tier 中第一个能力匹配的模型**
（`capability.py:239` `candidates[0]`）。也就是说：

    「模型清单里的顺序」= 「实际被选中的模型」

换模型时只改清单很容易漏看某个节点是否真的切过去了 ——
本脚本把 13 个节点的实际落点全部打印出来，作为切换的**验收证据**。

用法：
    uv run python scripts/diag_model_routing.py
"""

from __future__ import annotations

from storyforge.infra.llm.capability import get_registry

NODES = [
    "chapter_writer", "bible_architect", "volume_planner", "skeleton_planner",
    "detail_planner", "scene_extractor", "entity_resolver", "relation_extractor",
    "style_critic", "targeted_reviser",
    "learning_map", "learning_gap_check", "term_mapping",
]


def main() -> int:
    r = get_registry()

    print("=" * 82)
    print("注册表中的全部模型（顺序即路由优先级）")
    print("=" * 82)
    for i, m in enumerate(r.models):
        print(f"  [{i}] {m.id:<20} provider={m.provider:<8} tier={m.tier:<10} "
              f"json={getattr(m, 'supports_json_mode', '?')}")

    print()
    print("=" * 82)
    print("各节点的实际落点")
    print("=" * 82)
    print(f"{'节点':<22}{'模型':<20}{'tier':<11}思考策略")
    print("-" * 82)

    counts: dict[str, int] = {}
    for n in NODES:
        try:
            m, _p = r.route(n)
            pol = r.thinking_policy_for(n, m)
            counts[m.id] = counts.get(m.id, 0) + 1
            print(f"{n:<22}{m.id:<20}{m.tier:<11}{pol}")
        except Exception as e:  # noqa: BLE001
            print(f"{n:<22}★ {type(e).__name__}: {str(e)[:100]}")

    print()
    print("=" * 82)
    print("汇总（节点数 / 模型）")
    print("=" * 82)
    for mid, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {mid:<22} {c} 个节点")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
