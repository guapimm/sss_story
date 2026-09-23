"""取证：找出上次 scene_extractor 失败时的**完整**原始输出。

## 为什么要找完整输出

`client.py` 的报错只截前 300 字，而截断点在 **2887 字符处** ——
关键信息（模型到底怎么写的、是不是少了逗号/引号）恰好在被截掉的部分。

本项目踩过"用截断信息下结论"的坑，故先把完整输出取出来再判断。

用法：
    uv run python scripts/diag_last_llm_output.py [--node scene_extractor]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "storyforge.db"


def main() -> int:
    node = "scene_extractor"
    if "--node" in sys.argv:
        node = sys.argv[sys.argv.index("--node") + 1]

    if not DB.exists():
        print(f"数据库不存在：{DB}")
        return 1

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    tabs = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    print("表：", tabs)

    # 找与 LLM 调用/观测相关的表
    cand = [t for t in tabs if any(k in t.lower() for k in ("llm", "call", "observ", "meter"))]
    print("候选表：", cand)

    for t in cand:
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({t})")]
        has_node = any("node" in c.lower() for c in cols)
        has_raw = any(k in c.lower() for c in cols
                      for k in ("raw", "output", "content", "response", "text"))
        print(f"\n--- {t}")
        print(f"    列：{cols}")
        if not (has_node and has_raw):
            continue

        node_col = next(c for c in cols if "node" in c.lower())
        raw_col = next(c for c in cols
                       if any(k in c.lower() for k in ("raw", "output", "content", "response", "text")))
        rows = db.execute(
            f"SELECT * FROM {t} WHERE {node_col} = ? ORDER BY rowid DESC LIMIT 3",
            (node,),
        ).fetchall()
        print(f"    命中 {len(rows)} 条（node={node}）")
        for r in rows:
            d = dict(r)
            raw = d.get(raw_col) or ""
            print(f"    ── 长度 {len(str(raw))}")
            print(f"       {str(raw)[:200]}")

    # 兜底：直接把所有表的列名列出，便于人工定位
    print("\n" + "=" * 70)
    print("全部表的列名（便于人工定位）")
    print("=" * 70)
    for t in tabs:
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({t})")]
        print(f"  {t:<28} {cols}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
