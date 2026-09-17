"""伤势接线的**端到端**测试（0-7）—— 痛点 1「伤势永不恢复」的验收。

## 这条链为什么需要单独的测试

伤势恢复需要**四个环节**全部到位，任何一环断了整条链就静默失效：

    ① HealthScheduler 能排节拍          ✅ 一直有（315 行）
    ② attach_health_beats 能注入细纲     ✅ 一直有
    ③ 调度器拿得到**真实伤势**           ❌ repositories/ 是空目录
    ④ build_detail_batch 收到 schedule   ❌ books.py 没传

★ 而 ③④ 的失效是**静默**的：`if health_schedule is not None` 恒假，
`health_beats_injected` 恒 0，**不报错、不告警**。

因此本文件不断言"函数被调用了"，而是断言
**`health_beats_injected > 0`** —— 从落库的伤势一路走到注入计数。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from storyforge.domain.enums import (
    BookMode,
    EntityKind,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
)
from storyforge.graph.graphs.books import (
    GraphDeps,
    build_detail_batch_graph,
    build_health_schedule,
)
from storyforge.graph.nodes.bible_architect import bible_to_markdown, persist_bible
from storyforge.graph.nodes.volume_planner import persist_series_plan
from storyforge.infra.storage.repositories import (
    BookRepository,
    EntityRepository,
    InjuryRepository,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# build_health_schedule：纯函数层
# --------------------------------------------------------------------------- #


def test_build_health_schedule_returns_none_without_repo() -> None:
    """未接线仓储（纯文件模式）→ None，不假装成功。"""
    got = build_health_schedule(book_id=1, batch_start=1, batch_end=5, repo=None)
    assert got is None


def test_build_health_schedule_returns_none_when_no_injuries(db) -> None:
    """★ 库里没有伤势 → None（**不是**空 schedule）。

    调用方必须能区分"没有伤"与"算出了空节拍" ——
    返回空对象会让 `if health_schedule is not None` 为真却什么都不注入，
    又是"缺省即通过"的形态。
    """
    BookRepository(db).upsert(book_id=1, topic="t", genre="g")
    got = build_health_schedule(
        book_id=1, batch_start=1, batch_end=5,
        repo=InjuryRepository(db),
    )
    assert got is None


def test_build_health_schedule_produces_beats(db) -> None:
    """★ 有伤势 → 真的排出恢复节拍（这是链路 ③ 的验收）。"""
    books, entities, injuries = (
        BookRepository(db), EntityRepository(db), InjuryRepository(db)
    )
    books.upsert(book_id=1, topic="修仙", genre="xianxia", mode=BookMode.STORY)
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.SEVERE,
        status=InjuryStatus.NEW, chapter_no=1,
    )

    sched = build_health_schedule(
        book_id=1, batch_start=1, batch_end=10, repo=injuries
    )
    assert sched is not None, "有活跃伤势时必须排出节拍"
    assert sched.recovery_chapters, (
        "重度伤势在 10 章窗口内必须排出至少一个恢复章 —— "
        "否则恢复率仍然是 0%"
    )
    for ch in sched.recovery_chapters:
        assert 1 <= ch <= 10, f"恢复章 {ch} 落在批次窗口外"


def test_build_health_schedule_ignores_healed(db) -> None:
    """已痊愈的伤势不再排节拍（不然会反复催一条已经好了的伤）。"""
    books, entities, injuries = (
        BookRepository(db), EntityRepository(db), InjuryRepository(db)
    )
    books.upsert(book_id=1, topic="t", genre="g")
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    injuries.upsert(
        book_id=1, entity_id=hero.id, name="旧伤",
        kind=InjuryKind.WOUND, severity=InjurySeverity.SEVERE,
        status=InjuryStatus.HEALED, chapter_no=1,
    )
    assert build_health_schedule(
        book_id=1, batch_start=1, batch_end=10, repo=injuries
    ) is None


def test_build_health_schedule_survives_repo_error(db) -> None:
    """★ 读伤势失败**不得**让整批细纲失败（伤势是增强项，不是前置），
    但必须留痕 —— 不静默降级。"""

    class Boom:
        def list_active(self, book_id):
            raise RuntimeError("模拟 DB 故障")

    got = build_health_schedule(
        book_id=1, batch_start=1, batch_end=5, repo=Boom()  # type: ignore[arg-type]
    )
    assert got is None


# --------------------------------------------------------------------------- #
# 图级：health_beats_injected 必须 > 0（链路 ④ 的验收）
# --------------------------------------------------------------------------- #


def _series_plan():
    """复用 `test_graphs` 的夹具 —— **单一真相**。

    ⚠️ 不要在这里另造一份：`VolumePlan` 的构造口径（`chapter_start`/
    `chapter_end` 而非 `chapter_count`）会漂移，各测试各写一份必然假失败。
    """
    from test_graphs import series_plan

    return series_plan()


def test_detail_graph_injects_health_beats_from_db(db, tmp_path: Path) -> None:
    """★★ **0-7 的核心验收**：伤势从 DB 一路走到 `health_beats_injected > 0`。

    修复前：`books.py` 没传 `health_schedule` → 默认 None
    → `if health_schedule is not None` 恒假 → **恒 0**。

    ★ 注意本测试**同时**覆盖了两个断点：既要有伤势落库（③），
    又要把 schedule 传进 `build_detail_batch`（④）。
    只修 ④ 而没修 ③ 时，传进去的是空列表，`injected` 依然是 0。
    """
    from test_graphs import bible, detail_out, make_deps

    # ── ① 落库：书 → 实体 → 伤势 ──
    books, entities, injuries = (
        BookRepository(db), EntityRepository(db), InjuryRepository(db)
    )
    books.upsert(book_id=1, topic="修仙", genre="xianxia", mode=BookMode.STORY)
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.SEVERE,
        status=InjuryStatus.NEW, chapter_no=1,
    )

    # ── ② 图：把仓储接进 deps ──
    t = None
    deps, t = make_deps(tmp_path, {"detail": detail_out(3).model_dump_json()})
    deps.injuries = injuries                      # ★ 0-7 接线
    persist_series_plan(deps.store, _series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_detail_batch_graph(deps)
    out = asyncio.run(
        graph.ainvoke(
            {
                "book_id": 1, "current_volume": 1, "batch_start": 1, "batch_end": 3,
                "refs": {"bible_ref": "books/1/bible.md"},
            },
            {"configurable": {"thread_id": "t-health-wiring"}},
        )
    )

    assert out.get("detail_error", "") == "", f"细纲失败：{out.get('detail_error')}"
    injected = out.get("health_beats_injected", 0)
    assert injected > 0, (
        f"health_beats_injected 仍为 {injected} —— 伤势接线没生效。\n"
        f"若为 0，检查：① 伤势是否真的落库 ② build_detail_batch 是否收到 "
        f"health_schedule（修复前这里是恒 0）"
    )
    # state 里既然写了这个数，就必须真的非零（防"只写不读"的信号）
    assert isinstance(injected, int)


def test_detail_graph_writes_back_heal_scene_planned(db, tmp_path: Path) -> None:
    """★★ 细纲落盘后必须把 `heal_scene_planned` 置 True（链路最后一环）。

    为什么这一步是必需的：`Injury.urgency()`（`domain/health.py:272`）在
    `heal_scene_planned=False` 时返回 `None`（**不催促**）——
    这是消除旧项目死锁的关键设计。但若没有任何代码把它置 True，
    就会从"每章乱催"翻到"**永不催**"，恢复依然不发生。

    本测试断言：跑完细纲图之后，库里的伤势行真的被置位了。
    """
    from test_graphs import bible, detail_out, make_deps

    books, entities, injuries = (
        BookRepository(db), EntityRepository(db), InjuryRepository(db)
    )
    books.upsert(book_id=1, topic="修仙", genre="xianxia")
    hero = entities.get_or_create(book_id=1, name="陈砚", kind=EntityKind.CHARACTER)
    inj = injuries.upsert(
        book_id=1, entity_id=hero.id, name="左肩剑创",
        kind=InjuryKind.WOUND, severity=InjurySeverity.SEVERE,
        status=InjuryStatus.NEW, chapter_no=1,
    )
    assert inj.heal_scene_planned is False
    assert inj.urgency(5) is None, "置位前必须不催促（消除死锁的设计）"

    deps, _ = make_deps(tmp_path, {"detail": detail_out(3).model_dump_json()})
    deps.injuries = injuries
    persist_series_plan(deps.store, _series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    out = asyncio.run(
        build_detail_batch_graph(deps).ainvoke(
            {
                "book_id": 1, "current_volume": 1, "batch_start": 1, "batch_end": 3,
                "refs": {"bible_ref": "books/1/bible.md"},
            },
            {"configurable": {"thread_id": "t-heal-planned"}},
        )
    )
    assert out.get("detail_error", "") == ""

    after = [i for i in injuries.list_for_book(1) if i.id == inj.id][0]
    assert after.heal_scene_planned is True, (
        "细纲已落盘但 heal_scene_planned 仍为 False —— "
        "urgency() 会永远返回 None（从'乱催'翻到'永不催'）"
    )
    assert after.expected_heal_chapter is not None, (
        "置位时必须同时写 expected_heal_chapter（恢复目标章）"
    )


def test_detail_graph_without_repo_still_works(tmp_path: Path) -> None:
    """★ 未接线仓储时细纲链路**照常工作**（`injected` 为 0 但不报错）。

    这条防的是"为了修伤势把无 DB 的纯文件模式打挂" ——
    那是把增强项做成了硬前置。
    """
    from test_graphs import bible, detail_out, make_deps

    deps, _ = make_deps(tmp_path, {"detail": detail_out(3).model_dump_json()})
    assert deps.injuries is None, "默认不该有仓储（纯文件模式）"
    persist_series_plan(deps.store, _series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    out = asyncio.run(
        build_detail_batch_graph(deps).ainvoke(
            {
                "book_id": 1, "current_volume": 1, "batch_start": 1, "batch_end": 3,
                "refs": {"bible_ref": "books/1/bible.md"},
            },
            {"configurable": {"thread_id": "t-no-repo"}},
        )
    )
    assert out.get("detail_error", "") == ""
    assert out.get("health_beats_injected", 0) == 0
