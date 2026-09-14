"""思考强度 A/B 质量对比 —— 用本项目**已标定的打分卡**当裁判。

问题：「质量优先，思考强度开 low 的质量如何？」

方法：同一个细纲，各档思考强度各写 N 次，用 `scorecard.aggregate.evaluate`
打分（该打分卡的口径已逐字节复现旧项目 baseline，因此分数可比）。
同时记录 reasoning 占比，看"省下的 token"换来多少质量。

用法：
    uv run python scripts/ab_thinking.py --samples 3 --efforts default low high
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from storyforge.domain.outline import ChapterPlan, SceneBeat, WorldStateSnapshot  # noqa: E402
from storyforge.infra.llm.transport import extract_text, extract_usage  # noqa: E402
from storyforge.infra.prompts.library import ChapterContext, build_chapter_layers  # noqa: E402
from storyforge.scorecard.aggregate import evaluate  # noqa: E402

BASE_URL = os.environ.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.5")
KEY = os.environ.get("MIMO_API_KEY", "")

BIBLE = """【世界观圣经】
长明楼是一座始建于民国的旧档案馆，共五层，另有一处不列入楼层编号的十三层。
整栋楼实行值夜制度：每晚必须有一名值夜人留守至天亮，负责核对三盏壁灯的明暗次序、
应答零点的敲门声、把当夜发生的事逐条写进值班登记簿并签名留档。
楼内规则以文本形式存在，写在一本蓝布硬壳的《值夜手册》里，一旦落笔便只能覆盖不能擦除。
世界硬规则：
1. 规则一旦写入手册便不可擦除，只能覆盖
2. 午夜后应答唤名者视为交接完成
3. 值夜人不得越过门槛内侧的黄铜线
4. 壁灯必须保持一明两暗的次序
5. 十三层不属于长明楼的楼层编号
"""


def make_plan() -> ChapterPlan:
    return ChapterPlan(
        global_no=7,
        volume_no=1,
        title="值班簿上的第七个名字",
        plot="陈砚在值班簿上发现了自己的名字，笔迹是他自己的，但他从没写过",
        scenes=[
            SceneBeat(
                seq=0, goal="陈砚想核对手册上的值夜记录与自己记忆的差异",
                obstacle="周德海一再打断他，说这些记录不重要",
                outcome="陈砚在第三十七页发现一行自己的签名，日期是三年后",
                why_now="因为上一夜壁灯第一次出现两明一暗，秩序被打乱，他必须查清",
            ),
            SceneBeat(
                seq=1, goal="陈砚想问周德海三年前谁在这个岗位上",
                obstacle="周德海拒绝回答，转而问他有没有听见零点的敲门声",
                outcome="陈砚意识到周德海在回避，且对方知道的事比他多得多",
                why_now="因为签名上的日期指向周德海在场的时间段",
            ),
            SceneBeat(
                seq=2, goal="陈砚决定单独去地下二层查旧登记簿",
                obstacle="黄铜线拦在楼梯口，越过即违规",
                outcome="他停在线的这一侧，改用别的方式取证：抄下页码与墨色",
                why_now="因为规则第3条明确禁止越线，他必须在不违规的前提下推进",
            ),
            SceneBeat(
                seq=3, goal="陈砚试探周德海是否知道十三层",
                obstacle="周德海始终保持沉默，只用动作回应",
                outcome="周德海往后退了半步——这是全章唯一的破绽",
                why_now="因为陈砚提到签名日期时，周德海的手停了一下",
            ),
        ],
        entry_state=WorldStateSnapshot(
            location_name="长明楼三层值班室", story_time="第四日夜",
            present_names=["陈砚"], open_thread="壁灯次序在上一夜被打乱",
        ),
        exit_state=WorldStateSnapshot(
            location_name="长明楼三层值班室", story_time="第四日深夜",
            present_names=["陈砚", "周德海"], open_thread="周德海后退了半步",
        ),
        end_hook="周德海往后退了半步，脚跟碰到了黄铜线。",
    )


def render_detail(plan: ChapterPlan) -> str:
    out = []
    for i, s in enumerate(plan.scenes):
        out.append(
            f"{i + 1}. 目标：{s.goal}\n   阻碍：{s.obstacle}\n"
            f"   结果：{s.outcome}\n   为何是现在：{s.why_now}"
        )
    return "\n".join(out)


async def one_sample(effort: str | None, plan: ChapterPlan) -> dict:
    from langchain_openai import ChatOpenAI

    ctx = ChapterContext(
        global_no=plan.global_no, volume_no=plan.volume_no, title=plan.title,
        plot=plan.plot, plot_detail=render_detail(plan), end_hook=plan.end_hook,
        states="陈砚：值夜人，左肩有旧伤（未愈）。周德海：前任值夜人，隔门说话。",
    )
    layers = build_chapter_layers(
        bible_text=BIBLE, volume_block_text="【本卷】第1卷《值夜》：接手值夜",
        segment_block_text="", ctx=ctx, min_chars=4000, soft_max=7000,
    )
    msgs = layers.assemble()

    kwargs: dict = {
        "model": MODEL, "api_key": KEY, "base_url": BASE_URL,
        "timeout": 600, "max_retries": 0, "max_tokens": 8000,
    }
    if effort and effort != "default":
        kwargs["reasoning_effort"] = effort

    chat = ChatOpenAI(**kwargs)
    msg = await chat.ainvoke([
        {"role": "system", "content": msgs["system"]},
        {"role": "user", "content": msgs["user"]},
    ])
    text = extract_text(msg)
    usage = extract_usage(msg)
    card = evaluate(text, global_no=plan.global_no, plan=plan)
    return {
        "effort": effort or "default",
        "chars": card.literal.chars if card.literal else 0,
        "score": round(card.score, 4),
        "needs_review": card.needs_review,
        "triggers": [t.value for t in card.triggers],
        "dialogue": round(card.literal.dialogue_ratio, 4) if card.literal else None,
        "intra": round(card.literal.intra_redundancy, 4) if card.literal else None,
        "simile": card.literal.simile_count if card.literal else None,
        "cliche": len(card.literal.cliches) if card.literal else None,
        "cv": round(card.literal.sentence_len_cv, 4) if card.literal else None,
        "adherence": round(card.structural.outline_adherence, 3) if card.structural else None,
        "hook_landed": card.structural.hook_landed if card.structural else None,
        "unsupported": len(card.structural.unsupported_entities) if card.structural else 0,
        "reasoning": usage.get("reasoning_tokens"),
        "completion": usage.get("completion_tokens"),
        "prompt": usage.get("prompt_tokens"),
        "text": text,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--efforts", nargs="+", default=["default", "low", "high"])
    ap.add_argument("--out", default="doc/research/ab-thinking.json")
    args = ap.parse_args()

    if not KEY:
        print("需要 MIMO_API_KEY", file=sys.stderr)
        return 2

    plan = make_plan()
    results: list[dict] = []
    for effort in args.efforts:
        for i in range(args.samples):
            tag = f"{effort}#{i + 1}"
            print(f"  跑 {tag} …", flush=True)
            try:
                r = await one_sample(None if effort == "default" else effort, plan)
            except Exception as exc:  # noqa: BLE001
                print(f"    {tag} 失败：{type(exc).__name__}: {str(exc)[:160]}")
                continue
            r["sample"] = i + 1
            results.append(r)
            print(
                f"    {tag}: score={r['score']:.3f} chars={r['chars']} "
                f"reason={r['reasoning']}/{r['completion']} "
                f"需审={r['needs_review']} 触发={r['triggers']}"
            )

    print("\n=== 汇总（中位数）===")
    print(f"{'effort':<10}{'score':>8}{'chars':>8}{'dialogue':>10}{'intra':>8}"
          f"{'simile':>8}{'cv':>8}{'adherence':>11}{'reason%':>9}{'需审':>6}")
    summary = {}
    for effort in args.efforts:
        rows = [r for r in results if r["effort"] == effort]
        if not rows:
            continue
        def med(k):
            vals = [r[k] for r in rows if r.get(k) is not None]
            return statistics.median(vals) if vals else float("nan")
        rp = [100 * r["reasoning"] / r["completion"]
              for r in rows if r.get("reasoning") and r.get("completion")]
        row = {
            "n": len(rows),
            "score": med("score"), "chars": med("chars"), "dialogue": med("dialogue"),
            "intra": med("intra"), "simile": med("simile"), "cv": med("cv"),
            "adherence": med("adherence"),
            "reason_pct": statistics.median(rp) if rp else float("nan"),
            "need_review": sum(1 for r in rows if r["needs_review"]),
        }
        summary[effort] = row
        print(f"{effort:<10}{row['score']:>8.3f}{row['chars']:>8.0f}"
              f"{row['dialogue']:>10.4f}{row['intra']:>8.4f}{row['simile']:>8.0f}"
              f"{row['cv']:>8.3f}{row['adherence']:>11.3f}"
              f"{row['reason_pct']:>8.0f}%{row['need_review']:>4}/{row['n']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "samples": results},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\n明细已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
