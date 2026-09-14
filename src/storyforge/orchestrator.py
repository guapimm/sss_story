"""三级流水线协调器（决策 D-13）。

## 为什么需要它（而不是一个大图）

用户要求的三级重叠流水线：

    圣经 ─[C-1]→ 骨架批1 ─[C-2]→ 骨架批2 → ...
                    │              │
                    └→ 细纲批1 ────┴→ 细纲批2 → ...      ← 与骨架并行
                           │
                     第1章细纲就绪 ─[C-3]→ 正文1 → 正文2 → ...  ← 与细纲并行

**若做成单图的两个分支，`interrupt()` 会冻结整个图** —— 确认首章细纲时，
正在跑的骨架/细纲生产会被一起冻住，用户要的并行就没了。

因此：**LangGraph 图降为「工作单元」**，由本模块（纯 Python，无领域逻辑）调度。
每单元一个 `thread_id` → 崩溃恢复是**单元级**的（只重跑未完成的那一批）。

## 四条必须成立的不变量（P0-8 的验收内容）

1. **阶段内严格串行** —— 每阶段同时只有一个工作单元在跑。
   防的是旧项目"4 章并行起草时后写章按同一时刻账本构建上下文 → 看不到前章记忆"，
   以及 `join(timeout=1800)` 超时后 pregen 与 serial **双写同一章**。

2. **阶段间并行** —— 细纲与骨架可同时推进；正文与细纲可同时推进。

3. **人工确认只暂停该 worker** —— 不冻结其他 worker。
   这是与「单图 interrupt」的关键区别。

4. **崩溃恢复不重跑已完成单元** —— 单元级 checkpoint。

## 职责边界

本模块**只做编排**：队列、闸门、并发控制、确认路由、恢复。
**不含任何领域逻辑、不调用任何 LLM**（由 tests/contract 强制）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from storyforge.domain.enums import ConfirmPoint, PipelineStage, WorkUnitStatus

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 工作单元与执行器抽象
# --------------------------------------------------------------------------- #


@dataclass
class WorkItem:
    """一个工作单元 = 一次 LangGraph 图调用。"""

    stage: PipelineStage
    #: 批次序号（骨架/细纲）或章号（正文）
    seq: int
    volume_no: int | None = None
    #: ★ 所属书籍 —— **必须进幂等键**，否则两本书会共用同一个 LangGraph
    #: `thread_id`（checkpoint 串台），且并发两本书时会互相覆盖状态。
    book_id: int = 0
    #: 所属运行（可空；用于观测归因）
    run_id: int | None = None
    #: 图专属输入（`topic` / `genre` / `batch_start` / `batch_end` / `refs` …）。
    #: **只放轻量字段与 ref** —— 大产物一律走 `refs`（契约规则 5/19）。
    payload: dict[str, Any] = field(default_factory=dict)
    #: 幂等键：(book_id, stage, volume, seq)
    #: 防 LangGraph `#7417`：>180s 的调用会被**静默重放**（53 条评论 open）
    idempotency_key: str = ""
    #: ★ LangGraph thread_id —— 每单元独立 checkpoint 命名空间
    thread_id: str = ""
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    #: 人工确认点（非空表示正在等待）
    pending_confirm: ConfirmPoint | None = None
    result: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            vol = self.volume_no if self.volume_no is not None else "-"
            self.idempotency_key = (
                f"b{self.book_id}:{self.stage.value}:v{vol}:{self.seq}"
            )
        if not self.thread_id:
            self.thread_id = self.idempotency_key

    # -- 便捷访问（payload 的显式读数，避免调用方各写一遍 get）--------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


class UnitExecutor(Protocol):
    """执行一个工作单元的抽象。

    真实实现会调用 LangGraph 图；测试用哑执行器（见 tests/unit/test_orchestrator.py）。
    **本协议刻意保持极小** —— 协调器不需要知道图内部做什么。
    """

    async def run(self, item: WorkItem) -> Any: ...


#: 从执行结果里读出「待人工确认点」。
#:
#: ★ 由**图节点**按自身语义写出（如"只在首批骨架后"、`"只在第一章细纲就绪时"），
#: 协调器只做翻译，不重复判断 —— 否则同一规则会有两处实现，必然漂移。
ConfirmPointReader = Callable[[Any], "ConfirmPoint | None"]


def _no_confirm(result: Any) -> None:
    """默认：不识别任何确认点（哑执行器/测试用）。"""
    return None


#: 人工确认回调：返回 True 表示放行，False 表示拒绝/终止
ConfirmHandler = Callable[[WorkItem, ConfirmPoint], Awaitable[bool]]


class OrchestratorError(RuntimeError):
    pass


class GateTimeout(OrchestratorError):
    """闸门等待超时 —— 必须显式失败并告警，不能静默死等。

    旧项目预生成线程曾 **6 秒轮询空转**且不留痕（书 23：大纲完成 23:00:44
    → 首章文件 mtime 23:07:19，晚 7 分钟）。
    """


# --------------------------------------------------------------------------- #
# 队列与闸门
# --------------------------------------------------------------------------- #


@dataclass
class StageQueue:
    """单阶段的串行队列 + 唤醒机制。

    「阶段内严格串行」由 `workers=1` + 单队列保证。
    「等待不空转」由 `asyncio.Event` 保证（旧项目是 sleep 轮询）。
    """

    stage: PipelineStage
    items: list[WorkItem] = field(default_factory=list)
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    #: 是否已封口（不会再有新单元）
    closed: bool = False

    def push(self, item: WorkItem) -> None:
        self.items.append(item)
        self._event.set()

    def close(self) -> None:
        self.closed = True
        self._event.set()

    async def pop(self) -> WorkItem | None:
        """取下一个待执行单元。队列空且未封口时**等待**（不空转）。"""
        while True:
            for it in self.items:
                if it.status == WorkUnitStatus.PENDING:
                    return it
            if self.closed:
                return None
            self._event.clear()
            await self._event.wait()

    def has_pending(self) -> bool:
        return any(i.status == WorkUnitStatus.PENDING for i in self.items)


# --------------------------------------------------------------------------- #
# 协调器
# --------------------------------------------------------------------------- #


@dataclass
class OrchestratorConfig:
    """协调器参数（与 `config.PipelineSettings` 对应）。"""

    #: 正文最多领先细纲的章数 —— 防"正文跑偏导致后续细纲全废"
    chapter_lead_limit: int = 3
    #: 闸门最长等待（秒）
    gate_timeout_s: float = 3600.0
    #: 闸门轮询间隔（秒）—— 仅在上游无事件通知时使用（兜底）
    gate_poll_interval_s: float = 2.0


class Orchestrator:
    """三级流水线协调器。

    **只做编排**：队列、闸门、并发控制、确认路由、崩溃恢复。
    不含领域逻辑、不调用 LLM。
    """

    def __init__(
        self,
        executor: UnitExecutor,
        *,
        config: OrchestratorConfig | None = None,
        on_confirm: ConfirmHandler | None = None,
        confirm_point_of: ConfirmPointReader | None = None,
    ) -> None:
        self.executor = executor
        self.config = config or OrchestratorConfig()
        self.on_confirm = on_confirm
        #: 从执行结果里读确认点。默认不识别（哑执行器场景）。
        self.confirm_point_of: ConfirmPointReader = confirm_point_of or _no_confirm

        # ★ 队列必须覆盖**全部阶段**（由枚举派生）。
        # 旧项目 `ACTIVE_PHASES` 是手写白名单，漏了 `architecting`
        # （`pipeline.py:535` vs `:2009`）→ 架构师阶段崩溃后书**永久卡死**。
        # 手写白名单会漏，派生不会。
        self.queues: dict[PipelineStage, StageQueue] = {
            s: StageQueue(stage=s) for s in PipelineStage
        }
        #: 已完成的单元（供闸门判断）—— key = (stage, volume, seq)
        self.done: dict[tuple[str, int | None, int], WorkItem] = {}
        #: 正在等待人工确认的单元（**不阻塞其他 worker**）
        self.waiting_confirm: list[WorkItem] = []
        #: 记录每个阶段的并发峰值（用于断言"阶段内串行"）
        self.inflight_peak: dict[str, int] = {}
        self._inflight: dict[str, int] = {}
        self._stop = asyncio.Event()

    # -- 入队 ---------------------------------------------------------------

    def enqueue(self, item: WorkItem) -> WorkItem:
        self.queues[item.stage].push(item)
        return item

    def close_all(self) -> None:
        for q in self.queues.values():
            q.close()

    # -- 闸门 ---------------------------------------------------------------

    def _is_done(self, stage: PipelineStage, seq: int, volume: int | None = None) -> bool:
        return (stage.value, volume, seq) in self.done

    async def wait_for(
        self,
        stage: PipelineStage,
        seq: int,
        volume: int | None = None,
        *,
        timeout_s: float | None = None,
    ) -> bool:
        """等待某单元完成。**超时抛错**，不静默死等。

        用轮询 + 小睡实现（真实实现里上游完成会 push 事件）；
        关键是**超时必须显式失败并留痕**。
        """
        limit = timeout_s if timeout_s is not None else self.config.gate_timeout_s
        waited = 0.0
        interval = self.config.gate_poll_interval_s
        while not self._is_done(stage, seq, volume):
            if self._stop.is_set():
                return False
            if waited >= limit:
                raise GateTimeout(
                    f"等待 {stage.value} seq={seq} volume={volume} 超过 {limit}s。"
                    f"上游可能已失败 —— 不静默死等（旧项目 6 秒轮询空转且不留痕的教训）"
                )
            await asyncio.sleep(interval)
            waited += interval
        return True

    # -- 执行 ---------------------------------------------------------------

    async def _run_stage(self, stage: PipelineStage) -> None:
        """单阶段 worker 循环 —— **串行**执行本阶段所有单元。"""
        queue = self.queues[stage]
        while not self._stop.is_set():
            item = await queue.pop()
            if item is None:
                return
            # 「阶段内严格串行」：这里天然串行（一个循环，一次一个）
            n = self._inflight.get(stage.value, 0) + 1
            self._inflight[stage.value] = n
            self.inflight_peak[stage.value] = max(
                self.inflight_peak.get(stage.value, 0), n
            )
            item.status = WorkUnitStatus.RUNNING
            try:
                item.result = await self.executor.run(item)
                # ★ 图可能声明"到人工确认点了"（由节点按自身语义写出，
                # 适配器只翻译不判断）。确认**只暂停本 worker**（不变量 3）。
                point = self.confirm_point_of(item.result)
                if point is not None:
                    if not await self.request_confirm(item, point):
                        # 被拒绝 → 该单元失败，但**其他阶段继续**
                        continue
                    item.status = WorkUnitStatus.RUNNING
                item.status = WorkUnitStatus.DONE
                self.done[(stage.value, item.volume_no, item.seq)] = item
                logger.info("unit done: %s", item.idempotency_key)
            except Exception as exc:  # noqa: BLE001
                item.status = WorkUnitStatus.FAILED
                item.error = f"{type(exc).__name__}: {exc}"
                logger.warning("unit failed: %s -> %s", item.idempotency_key, item.error)
            finally:
                self._inflight[stage.value] = self._inflight.get(stage.value, 1) - 1

    async def request_confirm(
        self, item: WorkItem, point: ConfirmPoint
    ) -> bool:
        """发起人工确认 —— **只暂停该单元，不冻结其他 worker**（不变量 3）。"""
        item.pending_confirm = point
        item.status = WorkUnitStatus.WAITING_CONFIRM
        self.waiting_confirm.append(item)
        if self.on_confirm is None:
            # 无人处理 → 视为放行（测试/无人值守场景）；真实实现必须有 handler
            approved = True
        else:
            approved = await self.on_confirm(item, point)
        item.pending_confirm = None
        self.waiting_confirm.remove(item)
        if approved:
            item.status = WorkUnitStatus.PENDING
        else:
            item.status = WorkUnitStatus.FAILED
            item.error = f"人工确认被拒绝：{point.value}"
        return approved

    async def run(self) -> dict[str, Any]:
        """并发运行所有阶段 worker（阶段间并行、阶段内串行）。

        ★ worker 由**枚举派生** —— 新增阶段自动被调度，不会漏。
        """
        self._stop.clear()
        await asyncio.gather(
            *(self._run_stage(s) for s in PipelineStage),
            return_exceptions=True,
        )
        return self.report()

    def stop(self) -> None:
        self._stop.set()
        self.close_all()

    # -- 报告 ---------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        all_items = [i for q in self.queues.values() for i in q.items]
        return {
            "total": len(all_items),
            "done": sum(1 for i in all_items if i.status == WorkUnitStatus.DONE),
            "failed": sum(1 for i in all_items if i.status == WorkUnitStatus.FAILED),
            "pending": sum(1 for i in all_items if i.status == WorkUnitStatus.PENDING),
            # ★ 不变量 1 的证据：每阶段并发峰值必须为 1
            "inflight_peak": dict(self.inflight_peak),
            "stage_serial_ok": all(v <= 1 for v in self.inflight_peak.values()),
        }


# --------------------------------------------------------------------------- #
# 崩溃恢复
# --------------------------------------------------------------------------- #


def rebuild_queue(
    items: list[WorkItem],
) -> tuple[list[WorkItem], list[WorkItem]]:
    """崩溃恢复：区分「已完成」与「需重跑」，**不重跑已完成单元**（不变量 4）。

    返回 `(to_resume, already_done)`。

    旧项目对照：`recover_orphans` 把活跃阶段的书标为 interrupted 并清空
    卡死章节，但 `architecting` **不在** `ACTIVE_PHASES` 里
    （`pipeline.py:535` vs `:2009`）→ 该书**永久卡死**。
    本函数的输入是显式的工作单元列表（含状态），不存在"漏掉某个状态"的可能。
    """
    to_resume: list[WorkItem] = []
    already_done: list[WorkItem] = []
    for item in items:
        if item.status == WorkUnitStatus.DONE:
            already_done.append(item)
            continue
        # RUNNING 状态的单元在崩溃后不可信 → 必须重跑（但幂等键保证不重复计费）
        if item.status in (
            WorkUnitStatus.PENDING,
            WorkUnitStatus.RUNNING,
            WorkUnitStatus.ORPHANED,
        ):
            item.status = WorkUnitStatus.PENDING
            item.error = None
            to_resume.append(item)
        elif item.status == WorkUnitStatus.WAITING_CONFIRM:
            # 人工确认中断 → 保留确认点，重新入队等待
            item.status = WorkUnitStatus.PENDING
            to_resume.append(item)
        # FAILED 默认不自动重跑（需重试策略或人工介入）
    return to_resume, already_done


__all__ = [
    "WorkItem",
    "UnitExecutor",
    "ConfirmHandler",
    "ConfirmPointReader",
    "OrchestratorConfig",
    "Orchestrator",
    "StageQueue",
    "OrchestratorError",
    "GateTimeout",
    "rebuild_queue",
]
