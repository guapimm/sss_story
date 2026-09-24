"""捕获 story 打分卡的**行为基线**（重构前/后必须逐字段一致）。

## 为什么要先capture

本轮要把 `aggregate.py` 的维度从「硬编码在函数体」抽成 `DimensionSpec`
表驱动（这是 learn 打分卡的前提 —— 审计 §4.4 第 8 条）。
但那是**共享代码**：改错了会静默改变 story 的打分行为。

本项目对这类重构有明确纪律（拆 `doctor.py` 时凭记忆重写引入两个真 bug）：
**纯搬运式重构必须逐字段验证等价**。故先抓快照，重构后再抓一次，diff。

本脚本输出可直接粘进 `tests/unit/test_scorecard_parity.py` 的 Python 字面量。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 覆盖所有维度分支的输入 ──────────────────────────────────────────────
# 只做「字面层」时（无 plan）与「字面+结构层」（有 plan）走**不同代码路径**，
# 两条都必须覆盖。

#: 好章：有对话、够长、句长有变化
GOOD = (
    "“你来了。”老者抬眼，声音沙哑。\n"
    "林默没有立刻答话。他把那枚残破的玉牌放在石桌上，指尖在边缘停了半秒。"
    "屋外风穿过廊下，吹得灯焰歪向一侧，又慢慢立直。\n"
    "“我不明白。”他终于说，“师父当年为什么不带我走？”\n"
    "老者笑了笑,没答。他伸手把玉牌翻了个面，背面刻着极浅的纹路，"
    "像水波，又像某个人收紧的手指。\n"
    "“有些事，不是不带，是带不走。”他说，“你如今站到这里，"
    "说明你已经把该问的都问过一遍了。”\n"
    "林默沉默。他忽然想起十二岁那年的雨，想起自己被留在门槛外的那一夜。"
    "记忆像钝刀，割不破皮肉，却一直在那里压着。\n"
    "“那我该做什么。”他问。\n"
    "“回去。”老者说，“回青云宗。把你在外面学的东西，一样一样放下。”\n"
    "灯焰又晃了一下。这一次，它没有再立起来。\n"
) * 3

#: 差章：无对话、过短、句长均匀
BAD = "他走了。他停下。他回头。他看了看。他离开了。他继续走。他什么都没有说。"

#: 套话密集
CLICHE_HEAVY = (
    "他嘴角勾起一抹笑意，眼中闪过一丝精光。仿佛时间仿佛静止，"
    "不禁下意识地心头一紧。霎时，顿时，旋即，随即，不由自主地，"
    "他知道，这一刻，命运的齿轮开始转动。新的篇章就此开启。"
) * 2


def _snapshot(prose: str, *, label: str, with_plan: bool) -> dict:
    from storyforge.scorecard import evaluate

    plan = None
    if with_plan:
        from storyforge.domain.outline import ChapterPlan, SceneBeat

        plan = ChapterPlan(
            global_no=1, volume_no=1, title="测试章",
            plot="林默向老者求证当年的事",
            scenes=[
                SceneBeat(seq=0, location_name="石桌",
                          participant_names=["林默", "老者"]),
                SceneBeat(seq=1, location_name="回忆中的雨夜",
                          participant_names=["林默"]),
            ],
            end_hook="老者说：回青云宗，把学的东西放下",
        )

    card = evaluate(
        prose, global_no=5, plan=plan,
        prev_texts=[GOOD[:2000]] if with_plan else None,
    )
    return {
        "label": label,
        "score": round(card.score, 6),
        "passed": card.passed,
        "needs_review": card.needs_review,
        "forced_review": card.forced_review,
        "triggers": sorted(t.value for t in card.triggers),
        "dimensions": [
            [d.name, None if d.value is None else round(d.value, 6),
             d.passed, d.weight]
            for d in card.dimensions
        ],
    }


def main() -> int:
    cases = [
        ("good_no_plan", GOOD, False),
        ("good_with_plan", GOOD, True),
        ("bad_no_plan", BAD, False),
        ("bad_with_plan", BAD, True),
        ("cliche_no_plan", CLICHE_HEAVY, False),
    ]
    out = [_snapshot(p, label=lb, with_plan=wp) for lb, p, wp in cases]

    print("=" * 76)
    print("story 打分卡行为快照（重构前）")
    print("=" * 76)
    for s in out:
        print(f"\n── {s['label']}")
        print(f"   score={s['score']}  passed={s['passed']}  "
              f"forced={s['forced_review']}")
        print(f"   triggers={s['triggers']}")
        print(f"   维度 {len(s['dimensions'])} 个：")
        for name, val, ok, w in s["dimensions"]:
            print(f"     {name:<26} value={str(val):<10} passed={str(ok):<6} w={w}")

    dest = ROOT / "scripts" / "_scorecard_story_baseline.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(f"已写入：{dest.relative_to(ROOT)}")
    print("重构后重跑本脚本并 diff 该文件，即可证明 story 行为未变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
