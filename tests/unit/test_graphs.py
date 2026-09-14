"""图组装与协调器接线测试（P3-7）。

## 本文件防的是什么

1. **图能编译但跑不通** —— 只用「编译成功」当验收，是旧项目
   "配置写好了但 `enabled: false`" 的同一类失败。
   这里每个图都**真跑一遍**（用脚本化 transport，不打网络）。
2. **state 里混进大文本** —— checkpoint 会完整序列化每次节点执行的 state
   （官方承认 200 轮 5.3 GB）。本文件断言 state 与图输出里只有 ref 与小标量。
3. **确认点漏触发或错触发** —— C-2 只在首批、C-3 只在含第一章的批次。
   漏触发 = 人工确认形同虚设；错触发 = 每批都停下等人，流水线失去意义。
4. **幂等键串台** —— `thread_id` 若不含 book_id，两本书会共用 checkpoint。
"""

from __future__ import annotations

import asyncio

import pytest

from storyforge.domain.enums import ConfirmPoint, PipelineStage, WorkUnitStatus
from storyforge.graph.graphs.books import (
    GraphDeps,
    build_all,
    build_bible_graph,
    build_detail_batch_graph,
    build_skeleton_graph,
    build_volume_plan_graph,
    open_checkpointer,
    thread_config,
)
from storyforge.graph.graphs.executor import GraphUnitExecutor, _confirm_point_of
from storyforge.domain.outline import Milestone, SeriesPlan, VolumePlan
from storyforge.graph.nodes.bible_architect import bible_to_markdown, persist_bible
from storyforge.graph.nodes.volume_planner import persist_series_plan
from storyforge.infra.llm.client import (
    ClientConfig,
    LLMClient,
    MemoryIdempotencyStore,
)
from storyforge.infra.llm.schemas import (
    ArcOut,
    BibleOut,
    ChapterPlanOut,
    DetailBatchOut,
    MilestoneOut,
    SceneBeatOut,
    SeriesPlanOut,
    SkeletonOut,
    VolumePlanOut,
)
from storyforge.infra.storage.artifacts import ArtifactStore
from storyforge.orchestrator import Orchestrator, WorkItem

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 脚本化 transport（不打网络）
# --------------------------------------------------------------------------- #


class ScriptedTransport:
    """按**节点签名**分派的哑 transport（不打网络）。

    ⚠️ 不能用"第 N 次调用返回第 N 个响应"：协调器里多个阶段的 worker
    是**并发**的，调用顺序不确定（这正是要验证的并行性）。
    按 system prompt 里的节点特征串分派，与顺序无关。
    """

    #: 节点 → system prompt 里的特征串
    SIGNATURES: tuple[tuple[str, str], ...] = (
        ("bible", "世界观架构师"),
        ("volume_plan", "结构规划师"),
        ("skeleton", "长篇小说结构师"),
        ("detail", "细纲规划师"),
    )

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def _node_of(self, system: str) -> str:
        for node, sig in self.SIGNATURES:
            if sig in system:
                return node
        raise AssertionError(f"无法识别节点（system 前 80 字）：{system[:80]!r}")

    async def __call__(self, *, model, provider, system, user, **params):
        node = self._node_of(system)
        self.calls.append({"node": node, "system": system, "user": user})
        if node not in self.responses:
            raise AssertionError(f"没有为节点 {node!r} 准备脚本响应")
        return self.responses[node], {
            "prompt_tokens": 100, "completion_tokens": 50,
            "reasoning_tokens": 20, "cached_tokens": 40,
            "total_tokens": 150, "finish_reason": "stop",
        }


def make_deps(
    tmp_path, responses: dict[str, str] | None = None
) -> tuple[GraphDeps, ScriptedTransport]:
    t = ScriptedTransport(responses or {})
    client = LLMClient(
        t,
        config=ClientConfig(max_attempts=1, backoff_base_s=0.0, structured_retries=0),
        idempotency=MemoryIdempotencyStore(),
    )
    return GraphDeps(client=client, store=ArtifactStore(tmp_path)), t


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def bible() -> BibleOut:
    """圣经夹具 —— **复用**已按真实数据标定的那一份。

    ⚠️ 不要在这里另造一份更薄的：`validate_bible` 的总字数下限是按
    旧项目 5 本真实 `bible.txt`（4231~7631 字）标定的，
    各测试各写一份必然漂移，然后在校验器收紧时集体假失败
    （实测踩过一次：三个图测试同时挂掉，原因只是这份夹具太薄）。

    单一夹具 = 单一真相。
    """
    from test_bible_architect import good_bible

    return good_bible()


def vol_out(n: int, count: int = 200) -> VolumePlanOut:
    step = count // 10
    pts = sorted({max(1, min(count, 1 + i * step)) for i in range(10)})
    return VolumePlanOut(
        volume_no=n, title=f"第{n}卷", goal=f"目标{n}",
        new_locations=[f"{n}号新城", f"{n}号秘境"],
        new_factions=[f"{n}号宗门", f"{n}号世家"],
        antagonist=f"第{n}位大敌", power_ceiling=f"上限{n}",
        core_conflict=f"矛盾{n}",
        escalation_from_prev=f"从第{n - 1}卷的村斗升级为第{n}卷的城战",
        old_map_closure=f"第{n}卷旧地图收尾",
        chapter_count=count,
        milestones=[MilestoneOut(chapter_no=p, key=f"m{i}") for i, p in enumerate(pts)],
    )


#: 各卷的台阶（与骨架夹具一致 —— 骨架必须落实台阶的扩张要求，见
#: `skeleton_planner.validate_skeleton`）。
LADDERS: dict[int, dict[str, list[str]]] = {
    1: {"locs": ["长明楼", "旧城区闸口"], "facs": ["守夜会", "档案科"]},
    2: {"locs": ["西郊水塔", "南城地宫"], "facs": ["第九处", "搬运社"]},
}


def skeleton_out(volume_no: int = 1) -> SkeletonOut:
    loc0, loc1 = LADDERS[volume_no]["locs"]
    f0, f1 = LADDERS[volume_no]["facs"]
    return SkeletonOut(
        arcs=[
            ArcOut(key="十三条", kind="core",
                   description=f"陈砚在{loc0}里逐条验证残页上的十三条规则",
                   chapter_span=[1, 60]),
            ArcOut(key="继任", kind="core",
                   description="陈砚接过值夜簿，成为新的值夜人", chapter_span=[5, 120]),
            ArcOut(key="周德海", kind="relationship",
                   description=f"陈砚与周德海从{loc0}内的互相提防，到{loc1}以命相托",
                   chapter_span=[3, 90]),
            ArcOut(key="裴照", kind="relationship",
                   description="裴照隔门传话，两人由试探转为同盟", chapter_span=[10, 110]),
            ArcOut(key="封楼", kind="opposition",
                   description=f"{f0}要封死{loc0}，赌注是楼内住户的性命",
                   chapter_span=[20, 150]),
            ArcOut(key="十三层", kind="opposition",
                   description=f"{loc1}的存在与陈砚的记忆对抗，输了就忘记自己是谁",
                   chapter_span=[40, 180]),
        ],
        segments=["接手值夜", "头一桩委托", "封锁前夜", f"{loc1}的门开启"],
        characters=["陈砚", "周德海", "裴照"],
        factions=[f0, f1, "房客互助", "十三层住户"],
    )


def chapter_out(i: int) -> ChapterPlanOut:
    sc = [
        SceneBeatOut(
            goal=f"陈砚想查证第{i}件残页", obstacle=f"第{i}个阻碍：守夜人拦阻",
            outcome=f"第{i}个结果：拿到半页抄本",
            why_now=f"因为第{i - 1}拍的结果逼他必须现在来",
        )
        for i in range(4)
    ]
    return ChapterPlanOut(
        title=f"第{i}件委托",
        plot=f"陈砚处理第{i}件委托，牵出旧档案里的一页残纸",
        scenes=sc,
        entry_state={"location": "长明楼三层", "story_time": "第三日夜",
                     "present": ["陈砚"], "open_thread": "残页缺一页"},
        exit_state={"location": "长明楼三层", "story_time": "第四日晨",
                    "present": ["陈砚"], "open_thread": "第十三条被改过"},
        end_hook="他翻到背面，发现改动的墨色比正文新。",
    )


def detail_out(n: int) -> DetailBatchOut:
    return DetailBatchOut(chapters=[chapter_out(i) for i in range(1, n + 1)])


def series_plan() -> SeriesPlan:
    vols = []
    start = 1
    for n in (1, 2):
        v = VolumePlan(
            volume_no=n, title=f"第{n}卷", goal=f"目标{n}",
            new_locations=list(LADDERS[n]["locs"]),
            new_factions=list(LADDERS[n]["facs"]),
            antagonist=f"第{n}位大敌",
            chapter_start=start, chapter_end=start + 199,
            milestones=[Milestone(chapter_no=1, key="m0")],
        )
        vols.append(v)
        start += 200
    return SeriesPlan(book_id=1, total_volumes=2, target_chapters=400, volumes=vols)


# --------------------------------------------------------------------------- #
# 图能编译
# --------------------------------------------------------------------------- #


def test_all_graphs_compile(tmp_path) -> None:
    deps, _ = make_deps(tmp_path, ["{}"])
    graphs = build_all(deps)
    assert set(graphs) == {"bible", "volume_plan", "skeleton", "detail"}
    for name, g in graphs.items():
        assert g.get_name(), f"{name} 图必须有名字（便于日志与 trace）"
        assert "__start__" in g.get_graph().nodes


def test_thread_config_isolates_by_key() -> None:
    """★ `thread_id` 必须由幂等键派生 —— 单元级 checkpoint（不变量 4）。"""
    a = thread_config("b1:detail:v1:1-15")
    b = thread_config("b1:detail:v1:16-30")
    assert a["configurable"]["thread_id"] != b["configurable"]["thread_id"]
    assert a["recursion_limit"] > 0


def test_work_item_key_includes_book_id() -> None:
    """★ 幂等键必须含 book_id —— 否则两本书共用 checkpoint（串台）。"""
    a = WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1, book_id=1)
    b = WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1, book_id=2)
    assert a.idempotency_key != b.idempotency_key
    assert a.thread_id != b.thread_id


# --------------------------------------------------------------------------- #
# 图能真跑（脚本化 transport）
# --------------------------------------------------------------------------- #


async def test_bible_graph_runs_and_persists(tmp_path) -> None:
    deps, _ = make_deps(tmp_path, {"bible": bible().model_dump_json()})
    graph = build_bible_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "topic": "规则怪谈", "genre": "suspense", "refs": {}},
        thread_config("b1:bible"),
    )
    assert out["ok"] is True
    assert out["refs"]["bible_ref"] == "books/1/bible.md"
    assert "值夜手册" in deps.store.read_text("books/1/bible.md")


async def test_volume_plan_graph_runs_and_awaits_c4(tmp_path) -> None:
    """★ C-4：全卷台阶必须定稿确认后才开工骨架。"""
    deps, _ = make_deps(tmp_path)
    persist_bible(deps.store, 1, bible())

    plan_out = SeriesPlanOut(
        logline="x", total_volumes=2, target_chapters=400,
        volumes=[vol_out(1), vol_out(2)],
    )
    deps2, _ = make_deps(tmp_path, {"volume_plan": plan_out.model_dump_json()})
    deps2.store = deps.store
    graph = build_volume_plan_graph(deps2)
    out = await graph.ainvoke(
        {"book_id": 1, "topic": "t", "genre": "g",
         "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:volume_plan"),
    )
    assert out["gate_ok"] is True, out.get("gate_issues")
    assert out["series_plan_ref"] == "books/1/series_plan.json"
    assert out["awaiting_confirm"] == "C4_volume_ladder"
    assert out["total_volumes"] == 2
    assert out["repair_round"] == 0


async def test_skeleton_graph_first_batch_awaits_c2(tmp_path) -> None:
    """★ C-2：**首批**骨架完成后确认。"""
    deps, _ = make_deps(tmp_path, {"skeleton": skeleton_out(1).model_dump_json()})
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_skeleton_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "current_volume": 1, "batch_no": 1,
         "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:skeleton:v1:1"),
    )
    assert out["violations"] == []
    assert out["skeleton_ref"] == "books/1/skeleton/v001.json"
    assert out["awaiting_confirm"] == "C2_skeleton_first_batch"


async def test_skeleton_graph_later_batch_does_not_await(tmp_path) -> None:
    """★ 后续批次**不再**停下等人 —— 否则每批都等，流水线失去意义。"""
    deps, _ = make_deps(tmp_path, {"skeleton": skeleton_out(2).model_dump_json()})
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_skeleton_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "current_volume": 2, "batch_no": 2,
         "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:skeleton:v2:2"),
    )
    assert out["violations"] == []
    assert "awaiting_confirm" not in out


async def test_skeleton_graph_reports_missing_volume(tmp_path) -> None:
    """台阶里没有该卷 → 显式报违规（不静默跳过 —— 旧项目静默降级的老路）。"""
    deps, _ = make_deps(tmp_path, [skeleton_out().model_dump_json()])
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_skeleton_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "current_volume": 9, "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:skeleton:v9:1"),
    )
    assert out["violations"], "缺卷必须报错"
    assert "skeleton_ref" not in out


async def test_detail_graph_first_batch_awaits_c3(tmp_path) -> None:
    """★ C-3：**含第一章**的批次完成后确认（不是首批全部完成）——
    这样正文可立刻开工、与后续细纲并行。"""
    deps, _ = make_deps(tmp_path, {"detail": detail_out(3).model_dump_json()})
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_detail_batch_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "current_volume": 1, "batch_start": 1, "batch_end": 3,
         "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:detail:v1:1-3"),
    )
    assert out["lint_issues"] == []
    assert out["detail_refs"] == [
        "books/1/plans/00001.json",
        "books/1/plans/00002.json",
        "books/1/plans/00003.json",
    ]
    assert out["awaiting_confirm"] == "C3_first_detail"


async def test_detail_graph_later_batch_does_not_await(tmp_path) -> None:
    deps, _ = make_deps(tmp_path, {"detail": detail_out(3).model_dump_json()})
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_detail_batch_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "current_volume": 1, "batch_start": 4, "batch_end": 6,
         "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:detail:v1:4-6"),
    )
    assert out["lint_issues"] == []
    assert "awaiting_confirm" not in out


async def test_detail_graph_reports_lint_failure(tmp_path) -> None:
    """★ 失败批次**不落盘** —— 半成品落盘会被下游当成已就绪。"""
    bad = detail_out(3)
    bad.chapters[2].title = bad.chapters[0].title
    deps, _ = make_deps(tmp_path, {"detail": bad.model_dump_json()})
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))

    graph = build_detail_batch_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "current_volume": 1, "batch_start": 1, "batch_end": 3,
         "refs": {"bible_ref": "books/1/bible.md"}},
        thread_config("b1:detail:v1:1-3"),
    )
    assert out["lint_issues"], "必须报出问题"
    assert any("逐字重复" in i for i in out["lint_issues"])
    assert not out.get("detail_refs"), "失败批次不得落盘"
    assert deps.store.list_keys("books/1/plans") == [], "磁盘上不得留半成品"


async def test_state_carries_only_refs_not_text(tmp_path) -> None:
    """★ 图输出里只有 ref 与小标量 —— 不含正文/圣经原文。

    checkpoint 会完整序列化每次节点执行的 state；塞大文本 = checkpoint 膨胀。
    """
    deps, _ = make_deps(tmp_path, {"bible": bible().model_dump_json()})
    graph = build_bible_graph(deps)
    out = await graph.ainvoke(
        {"book_id": 1, "topic": "t", "genre": "g", "refs": {}},
        thread_config("b1:bible"),
    )
    for key, value in out.items():
        if isinstance(value, str):
            assert len(value) < 200, f"state 字段 {key} 疑似承载大文本（{len(value)} 字符）"
    assert "值夜手册" not in str(out.get("refs"))


# --------------------------------------------------------------------------- #
# checkpoint 真实可用（崩溃恢复的前提）
# --------------------------------------------------------------------------- #


async def test_checkpointer_roundtrip(tmp_path) -> None:
    """★ checkpoint 必须真的能写能读 —— 否则"单元级恢复"只是口号。"""
    db = str(tmp_path / "ckpt.db")
    deps, _ = make_deps(tmp_path, {"bible": bible().model_dump_json()})
    cfg = thread_config("b1:bible")

    async with open_checkpointer(db) as saver:
        compiled = build_bible_graph(deps)
        compiled.checkpointer = saver
        await compiled.ainvoke(
            {"book_id": 1, "topic": "t", "genre": "g", "refs": {}}, cfg
        )
        snap = await compiled.aget_state(cfg)
        assert snap is not None
        assert snap.values.get("ok") is True, "状态必须可从 checkpoint 读回"


async def test_checkpointer_isolates_threads(tmp_path) -> None:
    """不同 thread 的 checkpoint 互不干扰（两本书不串台）。"""
    db = str(tmp_path / "ckpt.db")
    deps, _ = make_deps(tmp_path, {"bible": bible().model_dump_json()})

    async with open_checkpointer(db) as saver:
        compiled = build_bible_graph(deps)
        compiled.checkpointer = saver
        cfg_a = thread_config("b1:bible")
        cfg_b = thread_config("b2:bible")
        await compiled.ainvoke({"book_id": 1, "topic": "t", "genre": "g", "refs": {}}, cfg_a)
        await compiled.ainvoke({"book_id": 2, "topic": "t", "genre": "g", "refs": {}}, cfg_b)

        a = await compiled.aget_state(cfg_a)
        b = await compiled.aget_state(cfg_b)
        assert a.values["book_id"] == 1
        assert b.values["book_id"] == 2


# --------------------------------------------------------------------------- #
# 执行器与协调器接线
# --------------------------------------------------------------------------- #


def test_confirm_point_reader_translates_enum() -> None:
    assert _confirm_point_of({"awaiting_confirm": "C2_skeleton_first_batch"}) == (
        ConfirmPoint.C2_SKELETON_FIRST_BATCH
    )
    assert _confirm_point_of({"awaiting_confirm": None}) is None
    assert _confirm_point_of({}) is None
    assert _confirm_point_of("不是字典") is None
    # 未知标识 → 忽略（**不得**当成已确认）
    assert _confirm_point_of({"awaiting_confirm": "C9_nonexistent"}) is None


async def test_executor_dispatches_bible_unit(tmp_path) -> None:
    deps, _ = make_deps(tmp_path, {"bible": bible().model_dump_json()})
    ex = GraphUnitExecutor(deps)
    item = WorkItem(stage=PipelineStage.BIBLE, seq=1, book_id=1,
                    payload={"topic": "规则怪谈", "genre": "suspense"})
    out = await ex.run(item)
    assert out["ok"] is True
    assert out["refs"]["bible_ref"] == "books/1/bible.md"


async def test_orchestrator_runs_bible_stage(tmp_path) -> None:
    """★ 协调器必须能调度**全部阶段**（含 BIBLE / VOLUME_PLAN）。

    旧项目 `ACTIVE_PHASES` 是手写白名单、漏了 `architecting`
    → 架构师阶段崩溃后书永久卡死。队列由枚举派生则不会漏。
    """
    deps, _ = make_deps(tmp_path, {"bible": bible().model_dump_json()})
    ex = GraphUnitExecutor(deps)
    orch = Orchestrator(ex, confirm_point_of=_confirm_point_of)
    item = WorkItem(stage=PipelineStage.BIBLE, seq=1, book_id=1,
                    payload={"topic": "t", "genre": "g"})
    orch.enqueue(item)
    orch.close_all()

    report = await orch.run()
    assert report["done"] == 1
    assert item.status == WorkUnitStatus.DONE


async def test_orchestrator_pauses_at_c2_without_freezing_detail(tmp_path) -> None:
    """★ 端到端：C-2 挂起期间，细纲 worker 必须继续推进（不变量 3 的真实形态）。

    这正是"单图 interrupt 会冻结整图"所不能实现的行为 —— 也是
    决策 D-13（图降为工作单元 + 纯 Python 协调器）的**唯一理由**。
    """
    # 骨架 1 批（会触发 C-2）+ 细纲 1 批（不该受影响）
    deps, _ = make_deps(
        tmp_path,
        {"skeleton": skeleton_out(1).model_dump_json(),
         "detail": detail_out(3).model_dump_json()},
    )
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))
    ex = GraphUnitExecutor(deps)

    confirmed = asyncio.Event()
    release = asyncio.Event()
    seen: list[ConfirmPoint] = []

    async def on_confirm(item: WorkItem, point: ConfirmPoint) -> bool:
        seen.append(point)
        if point is ConfirmPoint.C2_SKELETON_FIRST_BATCH:
            confirmed.set()
            await release.wait()      # 模拟人工审核耗时
        return True

    orch = Orchestrator(ex, on_confirm=on_confirm, confirm_point_of=_confirm_point_of)
    skeleton_item = WorkItem(stage=PipelineStage.SKELETON, seq=1, volume_no=1,
                             book_id=1, payload={"refs": {"bible_ref": "books/1/bible.md"}})
    detail_item = WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1, book_id=1,
                           payload={"batch_start": 1, "batch_end": 3,
                                    "refs": {"bible_ref": "books/1/bible.md"}})
    orch.enqueue(skeleton_item)
    orch.enqueue(detail_item)
    orch.close_all()          # 队列封口后 run() 才会终止（否则会一直等新单元）

    task = asyncio.create_task(orch.run())
    await asyncio.wait_for(confirmed.wait(), timeout=5)

    # ★ 确认挂起中：细纲必须能跑完（阶段间并行）
    for _ in range(200):
        if detail_item.status == WorkUnitStatus.DONE:
            break
        await asyncio.sleep(0.01)
    assert detail_item.status == WorkUnitStatus.DONE, (
        "C-2 挂起期间细纲必须继续推进 —— 否则并行流水线失去意义（不变量 3）"
    )
    assert skeleton_item.status == WorkUnitStatus.WAITING_CONFIRM

    release.set()
    report = await asyncio.wait_for(task, timeout=5)
    # 两个确认点都会到达：C-2（骨架首批）与 C-3（同批含第 1 章）
    assert set(seen) == {
        ConfirmPoint.C2_SKELETON_FIRST_BATCH,
        ConfirmPoint.C3_FIRST_DETAIL,
    }
    assert report["done"] == 2
    assert skeleton_item.status == WorkUnitStatus.DONE


async def test_orchestrator_rejected_confirm_fails_only_that_unit(tmp_path) -> None:
    """确认被拒绝 → 该单元失败，**其他阶段照常完成**。"""
    deps, _ = make_deps(
        tmp_path,
        {"skeleton": skeleton_out(1).model_dump_json(),
         "detail": detail_out(3).model_dump_json()},
    )
    persist_series_plan(deps.store, series_plan())
    deps.store.write_text("books/1/bible.md", bible_to_markdown(bible()))
    ex = GraphUnitExecutor(deps)

    async def reject(item: WorkItem, point: ConfirmPoint) -> bool:
        # 只拒绝骨架首批；细纲的 C-3 放行 —— 验证"拒绝只影响该单元"
        return point is not ConfirmPoint.C2_SKELETON_FIRST_BATCH

    orch = Orchestrator(ex, on_confirm=reject, confirm_point_of=_confirm_point_of)
    skeleton_item = WorkItem(stage=PipelineStage.SKELETON, seq=1, volume_no=1,
                             book_id=1, payload={"refs": {"bible_ref": "books/1/bible.md"}})
    detail_item = WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1, book_id=1,
                           payload={"batch_start": 1, "batch_end": 3,
                                    "refs": {"bible_ref": "books/1/bible.md"}})
    orch.enqueue(skeleton_item)
    orch.enqueue(detail_item)
    orch.close_all()

    report = await asyncio.wait_for(orch.run(), timeout=5)

    assert skeleton_item.status == WorkUnitStatus.FAILED
    assert "拒绝" in (skeleton_item.error or "")
    assert detail_item.status == WorkUnitStatus.DONE, "拒绝只影响该单元"
    assert report["done"] == 1 and report["failed"] == 1
