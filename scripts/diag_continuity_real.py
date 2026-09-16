"""缺陷 2 的**真实数据**分析 —— 判"地点跳变"假阳性的实际形态。

## 为什么现在能拿到数据了

此前真数据为 0（骨架阶段一直失败，细纲从未产出）。
本轮 `SkeletonOut.places` 修好后骨架通过，细纲**真的跑出来了** →
第一次拿到真实的相邻地点对。D-16 要求的"先看数据再动手"现在可以满足了。

## 待判定问题

`is_transition` 的置旗率是多少？
  · 若模型很少置旗 → 它按"没换地图"理解（prompt 与校验器定义冲突，改 C）
  · 若置旗频繁但仍在同场所间报警 → 名称回退路径太严（改 A/B）
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.domain.outline import WorldStateSnapshot  # noqa: E402

#: 上一轮真实 lint（来自 scripts/diag_detail_fail.py 的直接调用）
REAL_CASES: list[tuple[str, str, int]] = [
    ("档案管理科，档案库房外走廊", "王队长办公室", 2),
    ("林远家", "林远办公室", 3),
]

print("=" * 80)
print("① 真实 lint 案例逐条分析")
print("=" * 80)
for a, b, ch in REAL_CASES:
    probs = WorldStateSnapshot(location_name=a).continuity_problems(
        WorldStateSnapshot(location_name=b)
    )
    print(f"\n第{ch}章：{a}  →  {b}")
    print(f"  报警：{bool(probs)}")
    if probs:
        print(f"  消息：{probs[0][:90]}")

print()
print("=" * 80)
print("② 这两条算不算『硬切』？（人工判读）")
print("=" * 80)
print(
    "  案例1「档案管理科，档案库房外走廊」→「王队长办公室」\n"
    "     · 同一栋办公楼内的移动，中间**必然经过走廊** —— 这是正常换场\n"
    "     · 但它跨了**部门**（档案管理科 → 王队长办公室）\n"
    "     · 提示：地点名里带了**限定语**（「档案管理科，档案库房外走廊」）\n"
    "        说明模型在**细化**地点，而不是换地点\n"
    "\n"
    "  案例2「林远家」→「林远办公室」\n"
    "     · 从家到办公室 —— 两个**确实不同**的场所\n"
    "     · 但这是都市悬疑里最平常的换场（上班）\n"
    "     · 关键是**有没有写过渡**，而不是地点名是否相同\n"
)

print("=" * 80)
print("③ 根因再确认：校验器只比字符串，看不懂『同一个人的家/办公室』")
print("=" * 80)
print(
    "  `continuity_problems` 的判据是 `location_name.strip() != ...`，\n"
    "  它对**语义关系**一无所知：\n"
    "    · 不知道「档案库房外走廊」与「王队长办公室」在同一栋楼\n"
    "    · 不知道「林远家」与「林远办公室」属于同一个人\n"
    "  它只知道**两个字符串不一样**。\n"
    "\n"
    "  ★ 而 prompt 明确要求的是：「相邻章必须因果相接……禁止**无动机**跳变」\n"
    "    —— 要害是**动机（过渡）**，不是地点字符串相等。\n"
    "    校验器把『有动机的换场』与『硬切』**用同一个判据**处理了。\n"
)

print("=" * 80)
print("④ 三种改法的数据支撑度")
print("=" * 80)
print(
    "  A. 地点层级（LocationLevel + parent_id）—— 治本，但需要实体抽取先落地\n"
    "     现状：`LocationLevel` 有定义、**零实现**。本轮数据不足以建层级。\n"
    "\n"
    "  B. 同前缀/同后缀启发式（如都含「档案」视为同区）\n"
    "     风险：实测案例2「林远家」vs「林远办公室」**同前缀但确实是两个场所**\n"
    "     → 该启发式会把真实换场也放过（假阴性）。**数据不支持 B。**\n"
    "\n"
    "  C. ★ 让校验器检查**过渡是否被交代**，而不是地点名是否相等\n"
    "     依据：prompt 的原始要求就是『必须有过渡与动机』（已写在消息里！）\n"
    "     消息文案已经说「须写出可叙述的过渡与动机，否则就是硬切」——\n"
    "     但代码**根本没检查过渡**，它只比较了字符串。\n"
    "     → C 与 prompt 一致，且可零 LLM 判定（看换场章标记 / 场景拍点）。\n"
)
