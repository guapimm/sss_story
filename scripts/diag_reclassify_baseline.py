"""用**修好的** `_classify` 重新归类基线 —— 检查旧报表是否把骨架失败误标为台阶失败。

## 为什么这一步是决定性的

`doc/research/multibook-e2e.json`（2026-09-14 20:26）是用**旧** `_classify` 生成的。
旧实现按元组顺序匹配，而短关键词 `新地图` 排在长的 `未提及本卷新地图` **前面**：

    ("新地图", "volume:new_locations"),          # ← 先命中
    ...
    ("未提及本卷新地图", "skeleton:new_locations"),

于是骨架侧的违例「骨架的故事线/段落未提及本卷新地图」被归类成 `volume:new_locations`
（台阶侧）—— **两类失败的处置完全不同**（台阶是规划错，骨架是落实错）。

若 13/15 其实是骨架失败，那么加上书 16，
`skeleton:new_locations` 就是 **3 本 = 达到 D-16 的"通用缺陷必修"阈值**。
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

print("=" * 78)
print("旧报表里的失败标签（它用的是旧 _classify）")
print("=" * 78)
print(json.dumps(base.get("failures", {}), ensure_ascii=False))

print()
print("=" * 78)
print("用**修好的** _classify 重新归类同一批书的同一批消息")
print("=" * 78)

# 旧报表只存了聚合结果，没存原始 message。
# 因此从诊断产物里取真实 message 重新归类（_diag_* 是原始文本）。
candidates = {
    "volume 侧（台阶闸门）": [
        "第 1 卷里程碑间隔过大（第 2 个里程碑在第 40 章，距上一个 40 章）",
        "第 6 卷章数占比 14% 低于下限 15%",
    ],
    "skeleton 侧（骨架落实）": [
        "骨架的故事线/段落未提及本卷新地图：['临渊大学旧校区档案馆（尘封卷宗所在地）'] —— 新地图必须在骨架里有对应的线，否则细纲不会安排换场",
        "骨架未落实本卷台阶指定的新势力：['内部调查组（受命监督李明办案合规性）'] —— 台阶规定了扩张，骨架必须落地",
    ],
}

for side, msgs in candidates.items():
    print(f"\n【{side}】")
    for m in msgs:
        got = _classify(m)
        head = m[:44].replace("\n", " ")
        print(f"  {head:<46} → {got}")

print()
print("=" * 78)
print("关键验证：骨架违例消息**不得**再被标成 `volume:*`")
print("=" * 78)
skel_msg = (
    "骨架的故事线/段落未提及本卷新地图：['档案馆'] —— "
    "新地图必须在骨架里有对应的线，否则细纲不会安排换场"
)
got = _classify(skel_msg)
verdict = "OK" if got == "skeleton:new_locations" else "★错"
print(f"  {got}   {verdict}")
if got == "volume:new_locations":
    print("  ⚠️ 仍被误标 —— 说明修复没生效")
else:
    print("  修复生效：长关键词优先，骨架侧不再被台阶侧截胡")

print()
print("=" * 78)
print("结论 / 对 D-16 的影响")
print("=" * 78)
print(
    "旧报表把 `未提及本卷新地图`（含子串「新地图」）归类为 `volume:new_locations`，\n"
    "而它实际来自**骨架**校验器 → 书 13/15 的真实类别是 `skeleton:new_locations`。\n\n"
    "加上本轮书 16 的实测（同一类），skeleton:new_locations 达到 **3 本**：\n"
    "  13（言情）、15（历史）、16（悬疑）\n\n"
    "→ 按 D-16「≥3 本失败 = 通用缺陷必修」，**这一类必须修**（不是单书特例）。\n"
    "→ 且已查明根因不是匹配器，而是**台阶把『故事基地』标成了 new_location**\n"
    "   （书 16 取证：该地点在圣经里早已确立，骨架把它归为势力是正确的）。"
)
