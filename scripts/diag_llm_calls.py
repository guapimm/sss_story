"""取证：从 llm_calls 表读最近调用的 finish_reason / token 分布。

## 为什么这张表足以定位截断

`llm_calls` **刻意不存原始输出**（大文本不入观测表 —— 与契约规则 5
"State 不得含大文本"同一设计意图）。但它存了定位截断**必需**的字段：

    finish_reason        length = 被 max_tokens 截断；stop = 模型自己停的
    completion_tokens    实际输出多少
    reasoning_tokens     其中多少花在思考
    status / error_type  成功还是失败

判据是**确定性的**（API 返回的字段），不依赖对文本的猜测。

用法：
    uv run python scripts/diag_llm_calls.py [--node scene_extractor] [--limit 15]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "storyforge.db"


def main() -> int:
    node = None
    limit = 15
    if "--node" in sys.argv:
        node = sys.argv[sys.argv.index("--node") + 1]
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    if not DB.exists():
        print(f"数据库不存在：{DB}")
        return 1

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    where = "WHERE node_name = ?" if node else ""
    params: tuple = (node,) if node else ()
    rows = db.execute(
        f"""SELECT node_name, attempt_no, model, finish_reason, status,
                   completion_tokens, reasoning_tokens, prompt_tokens,
                   prompt_total_chars, elapsed_ms, error_type, started_at
            FROM llm_calls {where}
            ORDER BY id DESC LIMIT ?""",
        (*params, limit),
    ).fetchall()

    if not rows:
        print(f"无记录（node={node}）")
        # 看看有哪些 node 有记录
        for r in db.execute(
            "SELECT node_name, COUNT(*) c FROM llm_calls GROUP BY node_name ORDER BY c DESC"
        ):
            print(f"  {r['node_name']:<24} {r['c']} 次")
        return 0

    print("=" * 104)
    print("llm_calls 最近记录")
    print("=" * 104)
    print(f"{'node':<20}{'try':<5}{'finish':<9}{'status':<10}"
          f"{'out':>7}{'reason':>8}{'in':>7}{'chars':>8}{'ms':>8}  error")
    print("-" * 104)
    for r in rows:
        print(f"{r['node_name']:<20}{r['attempt_no']:<5}"
              f"{str(r['finish_reason']):<9}{str(r['status']):<10}"
              f"{r['completion_tokens'] or 0:>7}{r['reasoning_tokens'] or 0:>8}"
              f"{r['prompt_tokens'] or 0:>7}{r['prompt_total_chars'] or 0:>8}"
              f"{r['elapsed_ms'] or 0:>8}  {r['error_type'] or ''}")

    print()
    print("=" * 104)
    print("判读")
    print("=" * 104)
    n_len = sum(1 for r in rows if r["finish_reason"] == "length")
    n_fail = sum(1 for r in rows if (r["status"] or "") not in ("ok", "success", ""))
    print(f"  finish_reason=length : {n_len}/{len(rows)}  "
          f"→ {'★ 存在截断，需加大预算或减少输出量' if n_len else '无截断'}")
    print(f"  非成功状态           : {n_fail}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
