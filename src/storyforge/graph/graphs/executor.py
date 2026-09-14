"""把 LangGraph 图接到协调器上（决策 D-13 的落地层）。

## 职责

`orchestrator.py` 只做编排（队列/闸门/并发/确认/恢复），
`graph/graphs/books.py` 只做图组装。
本模块是两者的**适配器**：把 `WorkItem` 翻译成图的输入 state，
把图的输出写回 `WorkItem.result`，并把「待人工确认」翻译成 `ConfirmPoint`。

## ★ 为什么确认点由**图的输出**决定，而不是由适配器猜

`awaiting_confirm` 是图节点根据自身语义写出的（例如"只在首批骨架后"、
"只在第一章细纲就绪时"）。适配器只做**翻译**，不重复判断 ——
否则同一规则会有两处实现，必然漂移（旧项目 `since_ch` 口径漂移即此类）。
"""

from __future__ import annotations

import logging
from typing import Any

from storyforge.domain.enums import ConfirmPoint, PipelineStage
from storyforge.graph.graphs.books import GraphDeps, build_all, thread_config
from storyforge.orchestrator import WorkItem

logger = logging.getLogger(__name__)


class GraphUnitExecutor:
    """按 stage 分派到对应 LangGraph 图。

    每个 `WorkItem` 一次 `ainvoke`，`thread_id = item.idempotency_key`
    → 崩溃恢复是**单元级**的（不变量 4）。
    """

    def __init__(self, deps: GraphDeps) -> None:
        self.deps = deps
        self.graphs = build_all(deps)

    def _state_for(self, item: WorkItem) -> dict[str, Any]:
        """把工作单元翻译成图的输入 state。

        `item.payload` 由调用方按图的需要填（`topic`/`genre`/`batch_start`…），
        本方法只做**机械合并**：book/run/volume/batch_no 来自 WorkItem 本身，
        其余透传。

        state 里**只有轻量字段与 ref**（契约规则 5/19）。
        """
        state: dict[str, Any] = dict(item.payload)
        state.update(
            {
                "book_id": item.book_id,
                "run_id": item.run_id,
                "current_volume": item.volume_no or state.get("current_volume", 1),
                "batch_no": item.seq,
            }
        )
        state.setdefault("refs", {})
        return state

    @staticmethod
    def _stage_key(stage: PipelineStage) -> str:
        return {
            PipelineStage.BIBLE: "bible",
            PipelineStage.VOLUME_PLAN: "volume_plan",
            PipelineStage.SKELETON: "skeleton",
            PipelineStage.DETAIL: "detail",
        }[stage]

    async def run(self, item: WorkItem) -> Any:
        graph = self.graphs[self._stage_key(item.stage)]
        state = self._state_for(item)
        return await graph.ainvoke(state, thread_config(item.idempotency_key))


def _confirm_point_of(result: Any) -> ConfirmPoint | None:
    """从图输出里读出待确认点。

    `awaiting_confirm` 存的是**枚举名**（如 `C2_skeleton_first_batch`），
    由节点按自身语义写出；适配器只做翻译，不重复判断。
    """
    if not isinstance(result, dict):
        return None
    raw = result.get("awaiting_confirm")
    if not raw:
        return None
    try:
        return ConfirmPoint(raw)
    except ValueError:
        logger.warning("未知的确认点标识：%r（忽略，不静默当成已确认）", raw)
        return None


def make_executor(deps: GraphDeps) -> GraphUnitExecutor:
    return GraphUnitExecutor(deps)


__all__ = ["GraphUnitExecutor", "make_executor", "_confirm_point_of"]
