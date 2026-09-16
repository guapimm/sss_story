"""打印多书测试的中间/最终结果（用文件写，避开 PowerShell 引号问题）。"""

from __future__ import annotations

import json
import pathlib
import sys

p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                 else "doc/research/multibook-e2e.json")
d = json.loads(p.read_text(encoding="utf-8"))

print("=" * 96)
print(f"多书测试结果：{p}")
print("=" * 96)
print(f"{'书':<4}{'题材':<10}{'圣经':<6}{'台阶':<6}{'骨架':<6}{'细纲':<6}"
      f"{'章数':<5}{'卷':<4}{'秒':<7}")
for r in d["results"]:
    print(f"{r['book_id']:<4}{r['genre']:<10}"
          f"{str(r['bible_ok']):<6}{str(r['volume_ok']):<6}"
          f"{str(r['skeleton_ok']):<6}{str(r['detail_ok']):<6}"
          f"{r['detail_count']:<5}{r['volumes']:<4}{r['seconds']:<7.0f}")
    for f in r.get("failures") or []:
        print(f"       ✗ {f}")

print()
print("=" * 96)
print("失败聚合（D-16 判据）")
print("=" * 96)
fails = d.get("failures") or {}
if not fails:
    print("  全部通过 —— 无失败分类")
else:
    for label, books in sorted(fails.items(), key=lambda kv: -len(kv[1])):
        n = len(set(books))
        verdict = ("★ 通用缺陷（必修）" if n >= 3
                   else "待观察" if n == 2 else "单书特例（只记录）")
        print(f"  {label:<52} {n} 本 {sorted(set(books))}  {verdict}")

n_ok = sum(1 for r in d["results"] if r["detail_ok"])
print()
print(f"  细纲通过：{n_ok}/{len(d['results'])} 本")
