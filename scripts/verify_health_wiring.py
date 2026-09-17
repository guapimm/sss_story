"""0-7 真实链路验证：伤势从**真 DB** 一路走到恢复节拍。

与单测的区别：本脚本用 `Database.from_settings()` 打开**真实路径**的库
（`data/` 下的 sqlite），跑完整的
`建表 → 建书 → 建实体 → 落伤势 → 排节拍 → 注入细纲` 链路。

⚠️ 用独立临时目录，**不碰生产库**。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.config import Settings, configure, reset_settings  # noqa: E402
from storyforge.domain.enums import (  # noqa: E402
    BookMode, EntityKind, InjuryKind, InjurySeverity, InjuryStatus,
)
from storyforge.graph.graphs.books import build_health_schedule  # noqa: E402
from storyforge.infra.storage.db import Database  # noqa: E402
from storyforge.infra.storage.repositories import (  # noqa: E402
    BookRepository, EntityRepository, InjuryRepository,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sf_health_"))
    reset_settings()
    s = configure(Settings(data_dir=tmp / "data",
                           config_dir=ROOT / "config"))
    s.ensure_dirs()
    db = Database.from_settings(s)
    db.create_all()

    print("=" * 76)
    print("0-7 真实链路：建表 → 落伤势 → 排恢复节拍")
    print("=" * 76)
    print(f"  库文件：{s.db_path}")
    print(f"  表数  ：{len(db.list_tables())}")
    print(f"  外键强制：{db.foreign_keys_enabled()}  "
          f"journal={db.journal_mode()}")

    books, entities, injuries = (
        BookRepository(db), EntityRepository(db), InjuryRepository(db)
    )

    # ---- ① 建书（此前 books 是死表，全仓无写入路径）----
    book = books.upsert(
        book_id=1, topic="修仙：以知识碾压蛮力", genre="xianxia",
        mode=BookMode.STORY, target_chapters=400,
    )
    print(f"\n  ① 建书      ：id={book.id} mode={book.mode.value} "
          f"genre={book.genre}")

    # ---- ② 建实体（伤势 FK 到 entities，必须先有）----
    hero = entities.get_or_create(
        book_id=1, name="陈砚", kind=EntityKind.CHARACTER, chapter_no=1
    )
    print(f"  ② 建实体    ：id={hero.id} kind={hero.kind.value} "
          f"name={hero.canonical_name}")

    # ---- ③ 落伤势（此前 injuries 表零写入路径）----
    before = injuries.list_active(1)
    print(f"  ③ 落伤势前  ：活跃伤势 = {len(before)}")
    for name, kind, sev, ch in (
        ("左肩剑创", InjuryKind.WOUND, InjurySeverity.SEVERE, 1),
        ("内腑震伤", InjuryKind.WOUND, InjurySeverity.MODERATE, 1),
        ("神魂受创", InjuryKind.SEQUELA, InjurySeverity.SEVERE, 2),
    ):
        injuries.upsert(
            book_id=1, entity_id=hero.id, name=name, kind=kind,
            severity=sev, status=InjuryStatus.NEW, chapter_no=ch,
        )
    active = injuries.list_active(1)
    print(f"     落伤势后  ：活跃伤势 = {len(active)}")
    for i in active:
        print(f"       · {i.name}（{i.kind.value}/{i.severity.value}）"
              f" 自第 {i.since_chapter} 章")

    # ---- ④ 排恢复节拍（此前 health_schedule 恒 None）----
    print("\n  ④ 排恢复节拍：")
    total_beats = 0
    for start, end in ((1, 10), (11, 20), (21, 30)):
        sched = build_health_schedule(
            book_id=1, batch_start=start, batch_end=end, repo=injuries,
        )
        if sched is None:
            print(f"     批次 {start:>3}~{end:<3}：无节拍")
            continue
        rec = sched.recovery_chapters
        beats = sum(len(h.beats) for h in sched.chapters.values())
        total_beats += beats
        print(f"     批次 {start:>3}~{end:<3}：节拍 {beats:>2} 条，"
              f"恢复章 {rec if rec else '（无）'}")

    print(f"\n  合计注入节拍 = {total_beats} 条")
    if total_beats == 0:
        print("\n  ★ 零节拍 —— 伤势接线未生效（这是修复前的状态）")
        return 1

    # ---- ⑤ 恢复率：走完一次「痊愈」----
    print("\n  ⑤ 恢复率验证（旧项目实测恢复率 0%）：")
    injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.MINOR,
        status=InjuryStatus.HEALED, chapter_no=17,
    )
    healed = [i for i in injuries.list_for_book(1) if i.name == "左肩剑创"][0]
    print(f"     「左肩剑创」→ {healed.status.value}，"
          f"resolved_chapter={healed.resolved_chapter}")
    print(f"     活跃伤势 = {len(injuries.list_active(1))}（痊愈的不再催）")
    assert healed.resolved_chapter == 17

    ok = db.check_integrity()
    print(f"\n  完整性检查：{ok}")
    reset_settings()
    print("\n" + "=" * 76)
    print("  0-7 真实链路通过：伤势可落库、可排节拍、可痊愈")
    print("=" * 76)
    return 0 if ok == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
