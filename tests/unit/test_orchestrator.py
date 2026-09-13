"""协调器不变量测试 —— P0-8 的核心验收（决策 D-13）。

用**哑执行器**（不调 LLM、不碰 DB）验证四条不变量：

  1. 阶段内严格串行
  2. 阶段间并行
  3. 人工确认只暂停该 worker
  4. 崩溃恢复不重跑已完成单元

先用假数据证明编排正确，再接真实逻辑 —— 这样能在最便宜的时候发现编排问题。
"""

from __future__ import annotations

import asyncio

import pytest

from storyforge.domain.enums import ConfirmPoint, PipelineStage, WorkUnitStatus
from storyforge.orchestrator import (
    GateTimeout,
    Orchestrator,
    OrchestratorConfig,
    StageQueue,
    WorkItem,
    rebuild_queue,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 哑执行器 —— 记录并发峰值与执行顺序
# --------------------------------------------------------------------------- #


class RecordingExecutor:
    """记录每个阶段的并发数与完成顺序。"""

    def __init__(self, *, delay: float = 0.01, fail_on: set[str] | None = None) -> None:
        self.delay = delay
        self.fail_on = fail_on or set()
        self.inflight: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.order: list[str] = []
        self.completed: list[str] = []

    async def run(self, item: WorkItem) -> str:
        key = item.stage.value
        self.inflight[key] = self.inflight.get(key, 0) + 1
        self.peak[key] = max(self.peak.get(key, 0), self.inflight[key])
        self.order.append(item.idempotency_key)
        try:
            await asyncio.sleep(self.delay)
            if item.idempotency_key in self.fail_on:
                raise RuntimeError("boom")
            self.completed.append(item.idempotency_key)
            return f"result:{item.idempotency_key}"
        finally:
            self.inflight[key] = self.inflight.get(key, 1) - 1


class OverlapTrackingExecutor(RecordingExecutor):
    """记录每个阶段的「进入/退出」时间区间，用于**结构性**判定并行。

    为什么不测总耗时：机器繁忙时并行与串行的耗时差异会被压缩，造成假失败。
    测区间重叠则不受负载影响。
    """

    def __init__(self, *, delay: float = 0.01) -> None:
        super().__init__(delay=delay)
        #: {阶段: [(进入时刻, 退出时刻), ...]}
        self.spans: dict[str, list[tuple[float, float]]] = {}

    async def run(self, item: WorkItem) -> str:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            return await super().run(item)
        finally:
            t1 = loop.time()
            self.spans.setdefault(item.stage.value, []).append((t0, t1))

    def has_overlap(self, stage_a: str, stage_b: str) -> bool:
        """两个阶段的任意两次执行是否有时间重叠。"""
        for a0, a1 in self.spans.get(stage_a, []):
            for b0, b1 in self.spans.get(stage_b, []):
                if a0 < b1 and b0 < a1:
                    return True
        return False


def make_items(
    stage: PipelineStage, count: int, *, volume: int | None = 1
) -> list[WorkItem]:
    return [
        WorkItem(stage=stage, seq=i, volume_no=volume)
        for i in range(1, count + 1)
    ]


# --------------------------------------------------------------------------- #
# 不变量 1：阶段内严格串行
# --------------------------------------------------------------------------- #


async def test_invariant1_stage_is_serial() -> None:
    """阶段内严格串行：每阶段并发峰值必须为 1。

    防的是旧项目两个实测问题：
      · 4 章并行起草时，后写章按**同一时刻账本**构建上下文 → 看不到前章记忆
      · `_produce_first` 的 `join(timeout=1800)` 超时后 pregen 与 serial **双写同一章**
    """
    ex = RecordingExecutor(delay=0.005)
    orch = Orchestrator(ex)

    for it in make_items(PipelineStage.SKELETON, 6):
        orch.enqueue(it)
    orch.close_all()

    report = await orch.run()

    assert ex.peak[PipelineStage.SKELETON.value] == 1, (
        f"骨架阶段并发峰值必须为 1，实测 {ex.peak}"
    )
    assert report["stage_serial_ok"] is True
    assert report["done"] == 6
    # 完成顺序必须与入队顺序一致（串行 → 严格有序）
    assert ex.completed == [i.idempotency_key for i in make_items(PipelineStage.SKELETON, 6)]


async def test_invariant1_holds_under_multiple_stages() -> None:
    """即使三阶段并发运行，每阶段自身仍必须串行。"""
    ex = RecordingExecutor(delay=0.005)
    orch = Orchestrator(ex)

    for it in make_items(PipelineStage.SKELETON, 4):
        orch.enqueue(it)
    for it in make_items(PipelineStage.DETAIL, 4):
        orch.enqueue(it)
    for it in make_items(PipelineStage.CHAPTER, 4):
        orch.enqueue(it)
    orch.close_all()

    report = await orch.run()

    assert all(v == 1 for v in ex.peak.values()), f"各阶段峰值必须为 1，实测 {ex.peak}"
    assert report["done"] == 12
    assert report["stage_serial_ok"] is True


# --------------------------------------------------------------------------- #
# 不变量 2：阶段间并行
# --------------------------------------------------------------------------- #


async def test_invariant2_stages_run_in_parallel() -> None:
    """阶段间并行：细纲与骨架必须能**同时推进**。

    ## 判定方式（为什么不测耗时）

    初版用「总耗时 < 串行耗时之和」判定，实测在**全量测试套件的负载下会抖动**
    —— 机器繁忙时两阶段并行的总耗时可能接近串行值，造成假失败（已验证：单独跑 5/5 通过，
    全量跑偶发失败）。

    改为**测并发重叠**：让哑执行器记录「进入/退出」时间戳，
    只要两个阶段的执行区间有重叠，就证明它们真的在并行。
    这是**结构性判定**，不受机器负载影响。
    """
    ex = OverlapTrackingExecutor(delay=0.05)
    orch = Orchestrator(ex)

    n = 4
    for it in make_items(PipelineStage.SKELETON, n):
        orch.enqueue(it)
    for it in make_items(PipelineStage.DETAIL, n):
        orch.enqueue(it)
    orch.close_all()

    await orch.run()

    assert len(ex.completed) == 2 * n, "两阶段的单元都应执行完"
    # ★ 结构性断言：两阶段的执行区间必须重叠
    assert ex.has_overlap("skeleton", "detail"), (
        f"两阶段未并行：skeleton 区间={ex.spans.get('skeleton')}，"
        f"detail 区间={ex.spans.get('detail')}"
    )


# --------------------------------------------------------------------------- #
# 不变量 3：人工确认只暂停该 worker
# --------------------------------------------------------------------------- #


async def test_invariant3_confirm_does_not_block_other_workers() -> None:
    """人工确认只暂停该单元，**不冻结其他 worker**（与单图 interrupt 的关键区别）。

    这是 D-13 存在的根本原因：单图里 `interrupt()` 会冻结整个图，
    确认首章细纲时骨架/细纲生产也被冻住 → 用户要的并行就没了。

    验证方式：
      · 让细纲单元的**执行器**在跑到第 1 个单元时请求确认（真实图的行为）
      · 确认期间骨架必须继续完成自己的单元
    """
    ex = RecordingExecutor(delay=0.005)
    confirmed = asyncio.Event()
    release = asyncio.Event()

    async def on_confirm(item: WorkItem, point: ConfirmPoint) -> bool:
        confirmed.set()
        await release.wait()          # 模拟人工审核耗时
        return True

    orch = Orchestrator(ex, on_confirm=on_confirm)

    # 细纲第 1 个单元跑完后触发确认（模拟"首章细纲就绪"）
    detail_first = WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1)
    orch.enqueue(detail_first)

    # 骨架还有 3 批必须照常推进
    for it in make_items(PipelineStage.SKELETON, 3):
        orch.enqueue(it)
    orch.close_all()

    # 先让细纲第 1 单元执行完，再对它发起确认
    detail_task = asyncio.create_task(orch._run_stage(PipelineStage.DETAIL))
    await asyncio.sleep(0.03)                      # 等它完成
    assert detail_first.status == WorkUnitStatus.DONE

    confirm_task = asyncio.create_task(
        orch.request_confirm(detail_first, ConfirmPoint.C3_FIRST_DETAIL)
    )
    await confirmed.wait()                          # 确认已挂起

    # ★ 关键断言：确认挂起期间，骨架 worker 必须能跑完全部单元
    await orch._run_stage(PipelineStage.SKELETON)
    skel_done = [k for k in ex.completed if k.startswith("skeleton:")]
    assert len(skel_done) == 3, (
        f"确认期间骨架必须仍在推进（不变量 3），实测骨架完成 {skel_done}"
    )

    release.set()
    assert await confirm_task is True
    await detail_task


async def test_invariant3_confirm_leaves_other_stages_untouched() -> None:
    """确认挂起时，其他阶段的单元状态不受影响（未被冻结）。"""
    ex = RecordingExecutor(delay=0.005)
    release = asyncio.Event()

    async def on_confirm(item: WorkItem, point: ConfirmPoint) -> bool:
        await release.wait()
        return True

    orch = Orchestrator(ex, on_confirm=on_confirm)
    detail_item = WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1)
    skel_items = make_items(PipelineStage.SKELETON, 2)
    for it in skel_items:
        orch.enqueue(it)
    orch.close_all()

    confirm_task = asyncio.create_task(
        orch.request_confirm(detail_item, ConfirmPoint.C3_FIRST_DETAIL)
    )
    await asyncio.sleep(0.01)

    # 确认挂起中：其他阶段单元仍是 PENDING（可被调度），不是被冻结
    assert all(i.status == WorkUnitStatus.PENDING for i in skel_items)
    assert detail_item.status == WorkUnitStatus.WAITING_CONFIRM
    assert detail_item in orch.waiting_confirm

    await orch._run_stage(PipelineStage.SKELETON)
    assert len(ex.completed) == 2

    release.set()
    await confirm_task
    # 确认放行后回到 PENDING，可重新入队执行
    assert detail_item.status == WorkUnitStatus.PENDING
    assert detail_item not in orch.waiting_confirm


async def test_invariant3_rejected_confirm_marks_failed() -> None:
    """人工确认被拒绝 → 该单元标记失败，而不是静默继续。"""
    ex = RecordingExecutor()

    async def reject(item: WorkItem, point: ConfirmPoint) -> bool:
        return False

    orch = Orchestrator(ex, on_confirm=reject)
    item = WorkItem(stage=PipelineStage.CHAPTER, seq=1, volume_no=1)
    ok = await orch.request_confirm(item, ConfirmPoint.C5_GOLDEN_THREE)

    assert ok is False
    assert item.status == WorkUnitStatus.FAILED
    assert "拒绝" in (item.error or "")


# --------------------------------------------------------------------------- #
# 不变量 4：崩溃恢复
# --------------------------------------------------------------------------- #


def test_invariant4_resume_skips_completed_units() -> None:
    """崩溃恢复：**不重跑已完成单元**（单元级 checkpoint）。

    旧项目对照：`recover_orphans` 依赖 `ACTIVE_PHASES` 白名单，
    而 `architecting` **不在**该集合（`pipeline.py:535` vs `:2009`）
    → 架构师阶段崩溃后书**永久卡死**。
    本设计以工作单元状态为准，不存在"漏掉某个状态"的可能。
    """
    items = make_items(PipelineStage.DETAIL, 5)
    items[0].status = WorkUnitStatus.DONE
    items[1].status = WorkUnitStatus.DONE
    items[2].status = WorkUnitStatus.RUNNING     # 崩溃时在跑 → 不可信，需重跑
    items[3].status = WorkUnitStatus.PENDING
    items[4].status = WorkUnitStatus.FAILED      # 不自动重跑

    to_resume, already_done = rebuild_queue(items)

    assert len(already_done) == 2
    resume_seqs = sorted(i.seq for i in to_resume)
    assert resume_seqs == [3, 4], f"应重跑 3、4（RUNNING 与 PENDING），实测 {resume_seqs}"
    assert all(i.status == WorkUnitStatus.PENDING for i in to_resume)
    assert items[0].status == WorkUnitStatus.DONE, "已完成单元的状态不得被改动"


def test_invariant4_resume_preserves_pending_confirm() -> None:
    """人工确认中断后恢复：保留确认点重新入队，而不是跳过确认。"""
    items = make_items(PipelineStage.CHAPTER, 2)
    items[0].status = WorkUnitStatus.WAITING_CONFIRM
    items[0].pending_confirm = ConfirmPoint.C5_GOLDEN_THREE

    to_resume, _ = rebuild_queue(items)

    assert items[0] in to_resume
    assert items[0].status == WorkUnitStatus.PENDING
    assert items[0].pending_confirm == ConfirmPoint.C5_GOLDEN_THREE, (
        "恢复后必须保留确认点（否则会跳过黄金三章确认）"
    )


# --------------------------------------------------------------------------- #
# 闸门：等待不空转、超时必须报错
# --------------------------------------------------------------------------- #


async def test_gate_times_out_loudly_not_silently() -> None:
    """闸门超时必须**抛错**，不静默死等。

    旧项目预生成线程曾 **6 秒轮询空转**且不留痕：
    书 23「大纲完成 23:00:44 → 首章文件 mtime 23:07:19」晚了 7 分钟。
    """
    ex = RecordingExecutor()
    orch = Orchestrator(
        ex, config=OrchestratorConfig(gate_timeout_s=0.05, gate_poll_interval_s=0.01)
    )

    with pytest.raises(GateTimeout) as ei:
        await orch.wait_for(PipelineStage.SKELETON, seq=99)
    assert "不静默死等" in str(ei.value)


async def test_gate_returns_when_upstream_completes() -> None:
    ex = RecordingExecutor(delay=0.01)
    orch = Orchestrator(
        ex, config=OrchestratorConfig(gate_timeout_s=2.0, gate_poll_interval_s=0.01)
    )
    item = WorkItem(stage=PipelineStage.SKELETON, seq=1, volume_no=1)
    orch.enqueue(item)
    orch.close_all()

    waiter = asyncio.create_task(orch.wait_for(PipelineStage.SKELETON, seq=1, volume=1))
    await orch.run()
    assert await waiter is True


# --------------------------------------------------------------------------- #
# 队列行为
# --------------------------------------------------------------------------- #


async def test_queue_pop_waits_without_busy_loop() -> None:
    """队列空且未封口时 `pop` 必须**等待**（不空转轮询）。"""
    q = StageQueue(stage=PipelineStage.DETAIL)

    async def push_later() -> None:
        await asyncio.sleep(0.02)
        q.push(WorkItem(stage=PipelineStage.DETAIL, seq=1, volume_no=1))

    task = asyncio.create_task(q.pop())
    pusher = asyncio.create_task(push_later())
    item = await task
    await pusher

    assert item is not None and item.seq == 1


async def test_queue_pop_returns_none_when_closed_and_empty() -> None:
    q = StageQueue(stage=PipelineStage.DETAIL)
    q.close()
    assert await q.pop() is None


# --------------------------------------------------------------------------- #
# 失败处理与报告
# --------------------------------------------------------------------------- #


async def test_failed_unit_recorded_without_stopping_stage() -> None:
    """单个单元失败不应中断整个阶段（失败隔离）。"""
    ex = RecordingExecutor(delay=0.005, fail_on={"detail:v1:2"})
    orch = Orchestrator(ex)
    for it in make_items(PipelineStage.DETAIL, 4):
        orch.enqueue(it)
    orch.close_all()

    report = await orch.run()

    assert report["done"] == 3
    assert report["failed"] == 1
    failed = [i for q in orch.queues.values() for i in q.items
              if i.status == WorkUnitStatus.FAILED]
    assert "boom" in (failed[0].error or "")


def test_work_item_idempotency_key_is_deterministic() -> None:
    """幂等键必须确定性生成 —— 防 LangGraph `#7417` 静默重放导致重复计费。"""
    a = WorkItem(stage=PipelineStage.CHAPTER, seq=42, volume_no=2)
    b = WorkItem(stage=PipelineStage.CHAPTER, seq=42, volume_no=2)
    assert a.idempotency_key == b.idempotency_key == "chapter:v2:42"
    assert a.thread_id == a.idempotency_key, "每单元必须有独立 thread_id"


def test_report_shape() -> None:
    orch = Orchestrator(RecordingExecutor())
    r = orch.report()
    assert set(r) >= {"total", "done", "failed", "pending", "inflight_peak", "stage_serial_ok"}
    assert r["total"] == 0 and r["stage_serial_ok"] is True
