"""核对：debug 产物里有没有场景级 `location`（判断数据丢失的影响范围）。

若旧产物里没有 `location_name`，说明那次跑的是**修复前**的代码 ——
场景地点确实被丢掉了，需要重跑才能拿到。
"""

from __future__ import annotations

import json
import pathlib

p = pathlib.Path("data/artifacts/books/16/debug/plans_failed_00001-00005.json")
raw = json.loads(p.read_text(encoding="utf-8"))

print("=" * 70)
print("debug 产物里的 scene 字段")
print("=" * 70)
for no in sorted(raw, key=int)[:3]:
    ch = raw[no]
    print(f"\n第{no}章 keys: {sorted(ch)}")
    if ch.get("scenes"):
        s0 = ch["scenes"][0]
        print(f"  scenes[0] keys: {sorted(s0)}")
        print(f"  location_name  = {s0.get('location_name', '<缺失>')!r}")
        print(f"  participant_names = {s0.get('participant_names', '<缺失>')!r}")

has_loc = any(
    "location_name" in s
    for ch in raw.values()
    for s in (ch.get("scenes") or [])
)
print()
print("=" * 70)
print(f"结论：debug 产物里{'有' if has_loc else '**没有**'}场景级 location_name")
print("=" * 70)
if not has_loc:
    print(
        "  → 这份产物是**修复前**跑的，场景地点在转换时就被丢弃了，\n"
        "     所以无法用它核对『报警章是否真的换了地方』。\n"
        "     需要按修复后的代码**重跑**才能拿到该数据。"
    )
else:
    print("  → 场景地点已落盘，可用于机械判定（不再靠人judgment）。")
