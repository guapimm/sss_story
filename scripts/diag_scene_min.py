"""缺陷 8：软性质量偏好被当成**硬性 schema 拒绝**，一批全废。

## 实测（书 11，6 书回归）

```
细纲生成调用失败：结构化输出校验失败（3 次尝试）：
  - chapters.0.scenes：场景数 3 < 4（细纲要求 4~8 个分场景拍点）
  - chapters.1.scenes：场景数 3 < 4
```

15 章里 **2 章**只有 3 个场景 → **整批 15 章全部作废**（3 次重试全败）。

## 为什么这是缺陷（而不是"模型不听话"）

1. **层级错了**：`场景数 ≥ 4` 是**单章**的质量要求，
   却被放在 **schema 校验**里 —— 而 schema 校验失败 = **整批**失败。
   一章的瑕疵不该否决另外 14 章。

2. **已有正确的层级**：`lint_chapter` / `validate_batch` 本来就会查
   `场景数 < MIN_SCENES`，并把它记进 `lint_issues`（**按章**报告、
   **可增量修复**）。schema 层再拦一次，是**重复且更粗暴**的把关。

3. **与旧项目同一病灶**：`BibleOut` 曾因 `terms`（锦上添花字段）
   硬性非空而让整本书的圣经生成失败 —— 本轮已改为"归位/容忍"。
   这是**同一类错误**在细纲层的翻版。

## 本脚本核对
- schema 层是否仍在拒绝
- lint 层是否**确实**能抓到同一问题（若是，schema 层就是多余的硬拦）
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from storyforge.domain.outline import ChapterPlan, SceneBeat, WorldStateSnapshot
from storyforge.graph.nodes.detail_planner import MIN_SCENES, lint_chapter
from storyforge.infra.llm.schemas import DetailBatchOut, SceneBeatOut

print("=" * 80)
print("① schema 层：3 个场景会不会被拒？")
print("=" * 80)
scene = SceneBeatOut(goal="g", obstacle="o", outcome="r", why_now="w")
payload = {
    "chapters": [{
        "title": "t", "plot": "p" * 20, "beat_type": "normal",
        "is_transition": False,
        "scenes": [scene.model_dump()] * 3,          # ★ 只有 3 个
        "entry_state": {"location": "a"},
        "exit_state": {"location": "b"},
        "end_hook": "h",
    }],
}
try:
    DetailBatchOut.model_validate(payload)
    print("  通过（不再硬拦）")
except Exception as exc:  # noqa: BLE001
    print(f"  ★ 被拒：{str(exc)[:150]}")

print()
print("=" * 80)
print("② lint 层：同一个问题能不能抓到？（能 → schema 层是多余硬拦）")
print("=" * 80)
plan = ChapterPlan(
    global_no=1, volume_no=1, title="t", plot="p" * 20,
    scenes=[SceneBeat(seq=i, goal="g", obstacle="o", outcome="r", why_now="w")
            for i in range(3)],                            # ★ 同样只有 3 个
    entry_state=WorldStateSnapshot(location_name="a"),
    exit_state=WorldStateSnapshot(location_name="b"),
    end_hook="h",
)
issues = lint_chapter(plan)
print(f"  MIN_SCENES = {MIN_SCENES}")
print(f"  lint_chapter 结果：{issues}")
print()
if any("场景数" in i for i in issues):
    print("  ✅ lint 层**确实**能抓到 —— 所以 schema 层的硬拦是")
    print("     **重复把关，且粒度错误**（章级问题 → 批级失败）。")
else:
    print("  ⚠️ lint 层抓不到 —— 那 schema 层不能去掉，需另想办法。")

print()
print("=" * 80)
print("③ 影响面（按章报告 vs 整批作废）")
print("=" * 80)
print(
    "  schema 层拒绝：\n"
    "    · 15 章里 2 章不合格 → **15 章全部作废**（含 13 章合格内容）\n"
    "    · 3 次重试全败 → 该批次直接失败，不落盘\n"
    "    · 白烧 3 × 15 章的 token\n"
    "\n"
    "  lint 层拒绝：\n"
    "    · 按章记入 lint_issues → **只重写不合格的那 2 章**\n"
    "    · 13 章合格内容可保留（增量修复，旧项目已验证的正确方向）\n"
)
