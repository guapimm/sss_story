"""0-3 决策取证：`AI_SCENE_ENDINGS`（AI 画面收尾）在真实语料里到底命中多少？

旧项目把它接在 `needs_rewrite` 上（`style_metrics.py:131`），但书 23 命中 0/47。
本脚本扩到**旧项目全部成书**多个 book，回答：

  Q1 逐书命中率（末 60 字窗口，与旧项目口径一致）
  Q2 它与 `CLICHES` 的重叠度（有多少词其实已被套话表覆盖）
  Q3 若真命中，命中章是"结尾"还是"正文中段误伤"（抽样人工可读）
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.scorecard.literal import CLICHES  # noqa: E402

BOOKS_DIR = Path(r"E:\AI_project\story_llm\backend\storage\story\books")

AI_SCENE = ("嘴角", "弧度", "微微一笑", "勾起嘴角",
            "望向远方", "望向天际", "目光坚定", "目光深邃")


def main() -> int:
    # ---- 重叠度 ----
    print("=" * 76)
    print("Q2  AI_SCENE_ENDINGS 与 CLICHES（52 条套话表）的重叠")
    print("=" * 76)
    covered, novel = [], []
    for w in AI_SCENE:
        exact = w in CLICHES
        sub = [c for c in CLICHES if w in c or c in w]
        if exact or sub:
            covered.append((w, "完全命中" if exact else f"被 {sub} 覆盖"))
        else:
            novel.append(w)
    for w, why in covered:
        print(f"  · {w:<10} → 已被套话表覆盖（{why}）")
    for w in novel:
        print(f"  ★ {w:<10} → 套话表**未覆盖**（新增信息）")
    print(f"\n  重叠 {len(covered)}/8，独有 {len(novel)}/8")

    # ---- 逐书命中率 ----
    print()
    print("=" * 76)
    print("Q1  逐书命中率（末 60 字窗口）")
    print("=" * 76)
    if not BOOKS_DIR.exists():
        print("  旧项目 books 目录不可用")
        return 1
    rows = []
    tot_ch = tot_hit = 0
    word_hits: Counter[str] = Counter()
    samples: list[tuple[str, int, str, list[str]]] = []
    for book in sorted(BOOKS_DIR.iterdir(), key=lambda p: p.name):
        chdir = book / "chapters"
        if not chdir.is_dir():
            continue
        files = sorted(chdir.glob("v1-*.txt"))
        if len(files) < 3:
            continue
        hit = 0
        for f in files:
            t = f.read_text(encoding="utf-8", errors="ignore").strip()
            tail = t[-60:]
            hits = [w for w in AI_SCENE if w in tail]
            if hits:
                hit += 1
                for w in hits:
                    word_hits[w] += 1
                if len(samples) < 8:
                    samples.append((book.name, int(f.stem.split("-")[1]), tail[-40:], hits))
        rows.append((book.name, len(files), hit))
        tot_ch += len(files)
        tot_hit += hit
    for name, n, hit in rows:
        flag = "★" if hit else " "
        print(f"  {flag} 书 {name:>4}：{hit:>2}/{n:>3} 章"
              f"（{100 * hit / max(1, n):>5.1f}%）")
    print(f"\n  合计：{tot_hit}/{tot_ch} 章 = {100 * tot_hit / max(1, tot_ch):.2f}%")
    print(f"  词频：{dict(word_hits.most_common())}")

    # ---- Q3 抽样 ----
    print()
    print("=" * 76)
    print("Q3  命中样本（看是否为真·AI 画面收尾）")
    print("=" * 76)
    if not samples:
        print("  全语料零命中 —— 该规则在真实数据上**从未触发**")
    for book, ch, tail, hits in samples:
        print(f"  书 {book} 第 {ch} 章  命中{''.join(hits)}")
        print(f"    …{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
