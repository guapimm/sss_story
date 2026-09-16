"""A-2 诊断：连续性检查的假阳性范围 —— 用**可复现的合成对照**量化。

## 为什么用合成对照（而不是等真数据）

真数据要跑完整条链路（圣经→台阶→骨架→细纲），而当前骨架阶段还在失败，
拿不到任何 `exit_state`/`entry_state` 对（`data/artifacts/` 里零个细纲产物）。
D-16 要求"先看数据再动手"，指的是**改阈值**前必须有数据；
但"这个函数在哪些输入上报警"是**纯函数行为**，可以直接测定，
不需要等真数据。这两件事不该混为一谈。

## 要回答的问题

书 12 报过：

    第2章：地点跳变：本章结束于「青岚宗杂役弟子通铺」，
                      下一章开始于「青岚宗外门杂役区」

这是**假阳性**吗？——是。通铺与外门杂役区是**同一宗门内的两个院子**，
走过去就是正常换场。

本脚本量化：`continuity_problems` 对**同类移动**（同前缀、不同后缀）
一律报警，且报警率 100% —— 因为它只做字符串相等比较。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from storyforge.domain.outline import WorldStateSnapshot  # noqa: E402


def snap(name: str) -> WorldStateSnapshot:
    return WorldStateSnapshot(location_name=name)


#: (本章结束地, 下一章开始地, 这是不是**正常换场**)
CASES: list[tuple[str, str, bool]] = [
    # ── 同一地点内的移动（都不是"跳变"）──
    ("青岚宗杂役弟子通铺", "青岚宗外门杂役区", True),
    ("青岚宗外门杂役区", "青岚宗演武场", True),
    ("长明楼档案室", "长明楼值班室", True),
    ("长明楼二楼走廊", "长明楼一楼门厅", True),
    ("明城警察局重案组办公区", "明城警察局档案库", True),
    # ── 真正的硬切（该报警）──
    ("长明楼", "镇公所", False),
    ("青岚宗", "北境战场", False),
    ("明城警察局", "红枫巷商业街", False),
]


def main() -> int:
    print("=" * 78)
    print("案例".ljust(46), "应报警?", " 实际")
    print("=" * 78)

    false_pos = 0
    false_neg = 0
    for src, dst, is_normal_move in CASES:
        problems = snap(src).continuity_problems(snap(dst))
        fired = bool(problems)
        # 正常换场**不该**报警；硬切**该**报警
        expect_fire = not is_normal_move
        ok = fired == expect_fire
        if fired and is_normal_move:
            false_pos += 1
        if not fired and not is_normal_move:
            false_neg += 1
        label = f"{src} → {dst}"
        print(f"{label[:44]:<46} {str(expect_fire):<8} {fired}  {'OK' if ok else '★错'}")

    print()
    print(f"假阳性（正常换场被报警）：{false_pos}/{sum(1 for c in CASES if c[2])}")
    print(f"假阴性（硬切被放过）  ：{false_neg}/{sum(1 for c in CASES if not c[2])}")
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    print(
        "`continuity_problems` 只做**字符串相等**比较：任何不逐字相同的地点名\n"
        "都判为「地点跳变」。它对'同前缀不同后缀'（同一场所内的移动）\n"
        "没有豁免，因此**正常换场 100% 被误报**。\n\n"
        "这解释了书 12 的那条 lint：通铺 → 外门杂役区 属同一宗门内移动。\n\n"
        "★ 但**先不改**（D-16）。改法有两种，都需真数据支撑：\n"
        "  A. 地点层级（`LocationLevel` + `parent_id` 已有定义、零实现）——治本\n"
        "  B. 同前缀启发式（`青岚宗*` vs `青岚宗*` 视为同区）——止血，但会漏真跳变\n"
        "当前真数据为 0 条（骨架阶段未通过，细纲从未产出），无法判定\n"
        "哪种改法对真实分布更合适 —— 现在改就是在猜。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
