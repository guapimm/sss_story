"""用**真实产物**标定 learn 阈值（用户决策：拿真实样本再定）。

## 数据来源（书 993，均为真实产物）

  · 10 个知识点（真实 LLM 生成 + 逐条溯源 + 准确性核验通过）
  · 10 条平替修辞（真实 LLM 生成，修仙风格）
  · 12 条真实联网研究资料

## 标定什么

**能用地图数据直接标定的**（本脚本可做）：
  · 平替修辞的**术语长度分布** → 决定"正文出现原术语"的检测方式
  · 知识点的**名称/说明长度** → 决定知识密度的量级

**不能标定的（诚实标注）**：
  · 知识密度、解释充分性、递进连贯性 —— 需要**教学章正文**，
    而正文要等步骤 ⑥（learn 接入 story 规划链路）之后才有
  → 这些阈值目前取领域常识值，标注"待真实教学章复核"

★ 为什么不在没有正文时硬编一个"标定值"：那是**假标定** ——
本项目记录过「没有依据的阈值会退化成拍脑袋」，且
"看起来有依据"比"明说没依据"更危险。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    book = 993
    p = ROOT / "data/artifacts/books" / str(book) / "learning/map.json"
    if not p.exists():
        print(f"★ 找不到 {p} —— 先跑：")
        print(f"  uv run storyforge learn-map gen --book-id {book} "
              f"--topic 宋代点茶 --scale fast")
        return 1

    d = json.loads(p.read_text(encoding="utf-8"))
    points = d.get("points") or []
    maps = d.get("mappings") or []

    print("=" * 74)
    print(f"书 {book} 真实产物标定（{len(points)} 知识点 / {len(maps)} 平替修辞）")
    print("=" * 74)

    # ── ① 平替修辞：术语长度分布 ──────────────────────────────────────
    terms = [str(m.get("term", "")) for m in maps if m.get("term")]
    repls = [str(m.get("replacement", "")) for m in maps if m.get("replacement")]
    if terms:
        tl = sorted(len(t) for t in terms)
        rl = sorted(len(r) for r in repls)
        print()
        print("① 平替修辞（术语 → 世界内说法）")
        for t, r in list(zip(terms, repls))[:6]:
            print(f"     {t}  →  {r}")
        print(f"   术语字数：min={tl[0]} p50={tl[len(tl)//2]} max={tl[-1]}")
        print(f"   说法字数：min={rl[0]} p50={rl[len(rl)//2]} max={rl[-1]}")
        print()
        print(f"   ★ 实测判读（**修正了我此前的假设**）：术语是 **{tl[0]}~{tl[-1]} 字**")
        print("     的知识短语（如「调膏与七汤注水」），**不是**我预想的 2~5 字")
        print("     单词（「函数」「递归」）。原因：本主题是**领域知识**，")
        print("     知识点名天然是复合短语。")
        print()
        print("   ⇒ 对检测方式的影响：")
        print("     · 长子串匹配**可行**（越长越不易偶然命中）")
        print("     · 但**误报风险反而升高**：正文正常叙述可能包含")
        print("       术语的**部分**（如术语「候汤与熁盏」，正文写「候汤最难」）")
        print("       → 这是正常的（在讲这个知识点），不应判为「直出原术语」")
        print("     · 故 `bare_term` 检测**只对已确认映射里的术语整体**匹配，")
        print("       且**必须先剥掉说法**（否则说法含术语时自我误报）")

        # 检出一个真实风险：说法包含原术语时，子串匹配会连带命中
        overlap = [
            (t, r) for t, r in zip(terms, repls)
            if t and r and t in r
        ]
        if overlap:
            print(f"     ★ 实测有 {len(overlap)} 条**说法包含术语**：{overlap[:3]}")
            print("        → 检测必须先剥掉说法再找术语，否则自我误报")
        else:
            print("     ✓ 实测无「说法包含术语」的条目")

    # ── ② 知识点：名称与说明长度 ─────────────────────────────────────
    names = [str(x.get("name", "")) for x in points]
    sums = [str(x.get("summary", "")) for x in points]
    if names:
        nl = sorted(len(n) for n in names)
        sl = sorted(len(s) for s in sums)
        print()
        print("② 知识点")
        for x in points[:5]:
            print(f"     - {x.get('name')}  （{len(str(x.get('summary','')))} 字说明）")
        print(f"   名称字数：min={nl[0]} p50={nl[len(nl)//2]} max={nl[-1]}")
        print(f"   说明字数：min={sl[0]} p50={sl[len(sl)//2]} max={sl[-1]}")

    # ── ③ 溯源健康度（供交叉验证）────────────────────────────────────
    uniq = {tuple(sorted(x.get("source_urls") or [])) for x in points}
    with_ev = sum(1 for x in points if (x.get("evidence") or "").strip())
    print()
    print("③ 溯源（应与验收标准 §1.2.2 一致）")
    print(f"   来源组合数：{len(uniq)}（>1 = 逐条溯源）")
    print(f"   带 evidence：{with_ev}/{len(points)}")

    print()
    print("=" * 74)
    print("判读")
    print("=" * 74)
    print("  ✅ 可用真实数据标定：平替修辞的检测方式（子串匹配 + 剥离说法）")
    print("  ⬜ **不能**标定：知识密度/解释充分性/递进 —— 需教学章正文，")
    print("     而正文本轮尚不存在（依赖步骤 ⑥）")
    print("     → 这些阈值在 `LearnThresholds` 里标为『待真实教学章复核』")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
