"""查看 learn 学习地图的**真实内容**，为定验收标准提供事实依据。

## 为什么要看内容而不是只看结构

验收标准若只写"有 11 个知识点"就毫无意义 —— 真正要验的是
**知识点是否真是知识、平替修辞是否真的能懂**。

本脚本把真实样本摊开，供人工判读"哪些维度可量化、哪些不能"。
这是 D-16「先看数据再定判据」的落地。

用法：
    uv run python scripts/diag_learn_map_sample.py [book_id]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    bid = sys.argv[1] if len(sys.argv) > 1 else "991"
    p = ROOT / "data" / "artifacts" / "books" / bid / "learning" / "map.json"
    if not p.exists():
        print(f"★ 找不到：{p}")
        return 1

    d = json.loads(p.read_text(encoding="utf-8"))
    points = d.get("points") or []
    mappings = d.get("mappings") or []

    print("=" * 80)
    print(f"学习地图真实样本 —— 书 {bid} / 主题「{d.get('topic')}」/ 档位 {d.get('scale')}")
    print("=" * 80)

    print(f"\n【L-1 知识点】共 {len(points)} 个\n")
    for i, pt in enumerate(points, 1):
        if isinstance(pt, dict):
            pid = pt.get("point_id") or pt.get("id") or "?"
            name = pt.get("name") or pt.get("title") or "?"
            # ⚠️ 字段名是 `source_urls`（复数，tuple）—— 本脚本初版写成
            # `source` / `source_url`，于是**误报"来源 0%"**。
            # 这正是本项目记过的教训：诊断工具自身也会造假
            # （交接文档 §4.8），故这里把真实字段名一次列全。
            urls = pt.get("source_urls") or pt.get("source_url") or pt.get("source") or []
            if isinstance(urls, str):
                urls = [urls] if urls else []
            src = "、".join(str(u) for u in urls[:2])
            desc = pt.get("summary") or pt.get("description") or pt.get("content") or ""
            ev = pt.get("evidence") or ""
            print(f"  {i:>2}. [{pid}] {name}")
            if desc:
                print(f"      说明: {str(desc)[:100]}")
            print(f"      来源: {src or '（无）'}")
            if ev:
                print(f"      依据: {str(ev)[:100]}")
        else:
            print(f"  {i:>2}. {str(pt)[:110]}")

    print(f"\n【L-2 平替修辞】共 {len(mappings)} 条\n")
    for i, m in enumerate(mappings, 1):
        if isinstance(m, dict):
            term = m.get("term") or "?"
            repl = m.get("replacement") or "?"
            reason = m.get("reason") or m.get("note") or ""
            is_seed = m.get("is_seed")
            accepted = m.get("accepted")
            flag = "种子" if is_seed else "LLM"
            print(f"  {i:>2}. {term}  →  {repl}   [{flag}]"
                  f"{' ✓已确认' if accepted else ''}")
            if reason:
                print(f"      理由: {str(reason)[:96]}")
        else:
            print(f"  {i:>2}. {str(m)[:110]}")

    # 可量化维度统计（供定验收标准参考）
    print("\n" + "=" * 80)
    print("可量化维度盘点（用于定验收标准）")
    print("=" * 80)

    def _urls_of(pt: object) -> list[str]:
        if not isinstance(pt, dict):
            return []
        u = pt.get("source_urls") or pt.get("source_url") or pt.get("source") or []
        if isinstance(u, str):
            return [u] if u else []
        return [str(x) for x in u if x]

    with_src = sum(1 for pt in points if _urls_of(pt))
    with_ev = sum(1 for pt in points
                  if isinstance(pt, dict) and (pt.get("evidence") or "").strip())
    print(f"  知识点带来源     : {with_src}/{len(points)}"
          f"  ({with_src / len(points) * 100:.0f}%)" if points else "  —")
    print(f"  知识点带 evidence: {with_ev}/{len(points)}"
          f"  ({with_ev / len(points) * 100:.0f}%)" if points else "  —")

    seeds = sum(1 for m in mappings if isinstance(m, dict) and m.get("is_seed"))
    print(f"  平替修辞为种子   : {seeds}/{len(mappings)}"
          f"  （种子=未经 LLM，需人工调整）" if mappings else "  —")

    same = sum(1 for m in mappings if isinstance(m, dict)
               and m.get("term") == m.get("replacement"))
    print(f"  说法与原词相同   : {same}/{len(mappings)}  （>0 即为无效平替）")

    shortfall = (d.get("research_shortfall") or "").strip()
    print(f"  研究缺口报告     : {'有 —— ' + shortfall[:60] if shortfall else '（空）'}")

    print(f"\n  研究资料条数     : 见 research_cache.json（本轮 12 条真实片段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
