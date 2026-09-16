"""逐一验证**实测见过的全部形状**都能归一（含嵌套 dict 变体）。

## 为什么要穷举

`scene_extractor` 的 `state_updates` 在**同一本书、同一提示词**下，
四轮给出了**四种不同形状**：

  1. `[{"entity","attribute","value"}]`                        （仅字段名漂移）
  2. `[{"person","attribute","from","to"}]`                    （person + 变化）
  3. `{"李明": "情绪从疲惫变为恐惧和镇定"}`                       （dict，值是字符串）
  4. `{"李明": {"情绪状态": "从疲惫转为极度惊恐"}}`                （★ dict 套 dict）

单模型下这种"形状漂移"是**稳定的偏好不唯一**，靠重试改不动 ——
只能**全部接受并归一**（否则每一次重试都可能撞上另一种）。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: E402

BASE = {"summary": "李明发现军报封泥异常"}

CASES: list[tuple[str, dict]] = [
    ("规范形状", {"state_updates": [
        {"entity": "李明", "field": "位置", "value": "值房"},
    ]}),
    ("形状1：entity+attribute+value",
     {"state_updates": [{"entity": "李明", "attribute": "情绪",
                         "value": "从疲惫变为惊恐"}]}),
    ("形状2：person+attribute+from+to",
     {"state_updates": [{"person": "李明", "attribute": "情绪",
                         "from": "疲惫", "to": "惊恐"}]}),
    ("形状3：dict，值是字符串",
     {"state_updates": {"李明": "情绪从疲惫变为恐惧和镇定"}}),
    ("形状4：★ dict 套 dict",
     {"state_updates": {"李明": {"情绪状态": "从疲惫转为极度惊恐"}}}),
    ("形状5：dict，值是列表",
     {"state_updates": {"李明": ["情绪：惊恐", "目标：隐瞒发现"]}}),
    ("event：time/place/actors/后果",
     {"events": [{"time": "议和前夜", "place": "值房", "actors": ["李明"],
                  "description": "发现封泥异常", "后果": "产生怀疑"}]}),
]

print("=" * 88)
print("实测见过的全部形状 → 归一结果")
print("=" * 88)
ok = fail = 0
for name, payload in CASES:
    try:
        out = SceneExtractionOut.model_validate({**BASE, **payload})
        ok += 1
        print(f"\n✅ {name}")
        for s in out.state_updates:
            print(f"     entity={s.entity!r}")
            print(f"     field ={s.field!r}")
            print(f"     value ={s.value[:60]!r}")
        for e in out.events:
            print(f"     event: time={e.story_time!r} place={e.location!r} "
                  f"consequence={e.consequence[:30]!r}")
    except Exception as exc:  # noqa: BLE001
        fail += 1
        print(f"\n★ {name} —— 归一**失败**")
        print(f"     {str(exc)[:300]}")

print()
print("=" * 88)
print(f"结果：成功 {ok}/{len(CASES)}，失败 {fail}/{len(CASES)}")
print("=" * 88)
if fail == 0:
    print("  ✅ 全部形状都能归一 —— 模型怎么给都不会中断链路")
else:
    print("  ⚠️ 仍有形状过不去 —— 需要继续归一（或确认它确实该报错）")
