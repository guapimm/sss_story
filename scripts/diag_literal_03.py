"""诊断 0-3：`literal.py` 的两处缺陷的**实测影响**。

缺陷 A：`__all__` 声明了 `AI_SCENE_ENDINGS` 但无定义。
缺陷 B：`_SIMILE_PATTERNS` 里 `犹如` 正则重复两次 → simile 双计数。

本脚本问答三个问题：
  Q1 `犹如` 重复到底造成多少倍虚高？（书 23 实测）
  Q2 旧项目 `AI_SCENE_ENDINGS` 在书 23 末 60 字命中多少章？
  Q3 若把 `AI_SCENE_ENDINGS` 并入 `classify_ending` 的 summary 判据，
     会不会打破 golden 基线（summary 必须 = 0）？
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.scorecard.literal import (  # noqa: E402
    classify_ending,
    simile_count,
)

BOOK23 = Path(r"E:\AI_project\story_llm\backend\storage\story\books\23\chapters")

# 旧项目 style_metrics.py:52 权威定义
AI_SCENE_ENDINGS = ("嘴角", "弧度", "微微一笑", "勾起嘴角",
                    "望向远方", "望向天际", "目光坚定", "目光深邃")

# 修复后的比喻模式（删掉重复的 犹如）
_SIMILE_FIXED = (
    r"像[^，。；！？]{1,20}(?:一样|似的|般)",
    r"仿佛[^，。；！？]{1,20}(?:一样|似的|一般)",
    r"如同[^，。；！？]{1,20}(?:一样|似的|一般)",
    r"好似[^，。；！？]{1,20}(?:一样|似的|一般)",
    r"宛如[^，。；！？]{1,20}",
    r"犹如[^，。；！？]{1,20}",
    r"恰似[^，。；！？]{1,20}",
)


def main() -> int:
    files = sorted(BOOK23.glob("v1-*.txt"), key=lambda p: int(p.stem.split("-")[1]))
    if len(files) < 40:
        print(f"★ 书 23 章节不足（{len(files)} 章），无法实测")
        return 1
    texts = [f.read_text(encoding="utf-8", errors="ignore") for f in files]
    print(f"样本：书 23，{len(texts)} 章，"
          f"{sum(len(t) for t in texts):,} 字符\n")

    # ---------------- Q1 犹如双计数 ----------------
    print("=" * 76)
    print("Q1  `犹如` 正则重复 → simile_count 虚高多少？")
    print("=" * 76)
    cur = [simile_count(t) for t in texts]
    fix = [sum(len(re.findall(p, t)) for p in _SIMILE_FIXED) for t in texts]
    print(f"  当前实现  simile 总数 = {sum(cur)}")
    print(f"  去重后    simile 总数 = {sum(fix)}")
    print(f"  虚高                   = {sum(cur) - sum(fix)} 处"
          f"（{100 * (sum(cur) - sum(fix)) / max(1, sum(cur)):.1f}%）")
    over = [i for i, (a, b) in enumerate(zip(cur, fix)) if a != b]
    print(f"  受影响章数             = {len(over)}/{len(texts)}")
    # 阈值影响：hard=6 / target=3
    for name, th in (("target", 3), ("hard", 6)):
        broke = sum(1 for a in cur if a > th)
        broke_fix = sum(1 for a in fix if a > th)
        print(f"  超 {name}({th}) 章数：当前 {broke} → 去重 {broke_fix}"
              f"  {'← 虚增误报 ' + str(broke - broke_fix) + ' 章' if broke != broke_fix else ''}")

    # ---------------- Q2 AI_SCENE_ENDINGS 命中 ----------------
    print()
    print("=" * 76)
    print("Q2  旧项目 AI_SCENE_ENDINGS 在末 60 字命中多少章？")
    print("=" * 76)
    hit_ch: list[tuple[int, str]] = []
    for i, t in enumerate(texts):
        tail = t.strip()[-60:]
        hits = [w for w in AI_SCENE_ENDINGS if w in tail]
        if hits:
            hit_ch.append((i + 1, "/".join(hits)))
    print(f"  命中章数 = {len(hit_ch)}/{len(texts)}")
    for ch, w in hit_ch[:12]:
        print(f"    第 {ch:>2} 章：{w}")
    if len(hit_ch) > 12:
        print(f"    … 另 {len(hit_ch) - 12} 章")
    # 各词命中分布
    wc: Counter[str] = Counter()
    for t in texts:
        tail = t.strip()[-60:]
        for w in AI_SCENE_ENDINGS:
            if w in tail:
                wc[w] += 1
    print(f"  词频：{dict(wc.most_common())}")

    # ---------------- Q3 并入 summary 会不会破基线 ----------------
    print()
    print("=" * 76)
    print("Q3  若把 AI_SCENE_ENDINGS 并入 classify_ending 的 summary 判据？")
    print("=" * 76)
    got = Counter(classify_ending(t).value for t in texts)
    print(f"  当前分布（golden 基线）：{dict(got.most_common())}")
    print(f"  基线要求：mid_action 23 / other 21 / omen 2 / dialogue 1"
          f" / summary 0 / time_skip 0")

    # 模拟：summary 命中再加上 AI_SCENE 末 60 字
    # classify_ending 的优先级是 time>omen>summary>dialogue>action，
    # 所以只有当前落到 dialogue/mid_action/other 的章才可能被改写。
    would_flip = []
    for i, t in enumerate(texts):
        cur_e = classify_ending(t)
        tail = t.strip()[-60:]
        ai_hit = any(w in tail for w in AI_SCENE_ENDINGS)
        if ai_hit and cur_e.value in ("other", "mid_action", "dialogue"):
            would_flip.append((i + 1, cur_e.value))
    print(f"\n  ★ 会从 other/mid_action/dialogue 翻转为 summary 的章数"
          f" = {len(would_flip)}")
    for ch, e in would_flip[:10]:
        print(f"    第 {ch:>2} 章：{e} → summary")
    if len(would_flip) > 10:
        print(f"    … 另 {len(would_flip) - 10} 章")
    if would_flip:
        print("\n  ★★ 结论：并入会打破 golden 测试"
              "（`assert got.get('summary', 0) == 0`）→ 不可并入。")
    else:
        print("\n  结论：并入不影响基线，但仍是语义混叠。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
