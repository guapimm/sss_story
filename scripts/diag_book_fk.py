"""取证：入库时 FOREIGN KEY constraint failed —— 书 19 是否真的存在于 books 表。

## 现象

`storyforge write --book-id 19 --chapters 1` 跑完了全章（4581 字），
但最后入库失败：

    sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed
    INSERT INTO entities (book_id, ...) VALUES (19, '人物', '林默', ...)

## 要回答

1. `books` 表里有 id=19 吗？（没有 → 外键必然失败）
2. 若没有，那 `data/artifacts/books/19/` 的产物是哪来的？
   （可能是直接跑节点产生的，绕过了建书步骤）

这是**确定性问题**（查表即可），不靠猜。

用法：
    uv run python scripts/diag_book_fk.py [book_id]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "storyforge.db"
ART = Path(__file__).resolve().parent.parent / "data" / "artifacts" / "books"


def main() -> int:
    bid = int(sys.argv[1]) if len(sys.argv) > 1 else 19

    print("=" * 72)
    print(f"诊断 book_id={bid} 的外键问题")
    print("=" * 72)

    if not DB.exists():
        print(f"★ 数据库不存在：{DB}")
        return 1

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    # ① books 表里有哪些书
    rows = db.execute(
        "SELECT id, title, mode, genre, status, current_chapter FROM books ORDER BY id"
    ).fetchall()
    print(f"\n① books 表共 {len(rows)} 本：")
    for r in rows:
        mark = "  ★ 目标" if r["id"] == bid else ""
        print(f"   id={r['id']:<5} {str(r['title'])[:24]:<26} "
              f"mode={r['mode']:<6} status={str(r['status'])[:10]:<12}"
              f"ch={r['current_chapter']}{mark}")

    exists = any(r["id"] == bid for r in rows)
    print(f"\n   → book_id={bid} 在 books 表中：{'存在 ✓' if exists else '★ 不存在'}")

    # ② 该书的产物目录
    d = ART / str(bid)
    print(f"\n② 产物目录 {d}：")
    if d.exists():
        files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
        print(f"   存在，{len(files)} 个文件")
        for f in files[:12]:
            print(f"     - {f}")
    else:
        print("   ★ 不存在")

    # ③ 各子表里有没有该书的残留（孤儿行）
    print(f"\n③ 各表里 book_id={bid} 的行数（孤儿行会阻塞重建）：")
    for t in ("chapters", "entities", "volumes", "events", "llm_calls",
              "work_units", "foreshadowings", "injuries", "locations",
              "runs", "entity_states", "entity_relations"):
        try:
            n = db.execute(f"SELECT COUNT(*) FROM {t} WHERE book_id = ?", (bid,)).fetchone()[0]
            flag = " ← 需清理" if n and not exists else ""
            print(f"   {t:<22} {n}{flag}")
        except sqlite3.OperationalError as e:
            print(f"   {t:<22} （无 book_id 列：{e}）")

    print("\n" + "=" * 72)
    print("判读")
    print("=" * 72)
    if not exists:
        print(f"  ★ books 表缺 id={bid} —— 外键失败的直接原因。")
        print(f"    两条修法：")
        print(f"      A) 建这本书再写：`storyforge plan`/建书命令（推荐，产物与库一致）")
        print(f"      B) 直接往 books 插一行，复用盘上已有产物")
    else:
        print(f"  books 表有 id={bid}，外键失败另有原因（检查参数里的其它外键）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
