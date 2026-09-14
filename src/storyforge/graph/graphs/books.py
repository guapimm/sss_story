"""LangGraph 图组装 —— **每个图是一个「工作单元」**（决策 D-13）。

## 为什么不是一个大图

用户要求的三级重叠流水线：

    圣经 ─[C-1]→ 骨架批1 ─[C-2]→ 骨架批2 → ...
                    │              │
                    └→ 细纲批1 ────┴→ 细纲批2 → ...      ← 与骨架并行
                           │
                     第1章细纲就绪 ─[C-3]→ 正文1 → 正文2 → ...

**若做成单图的两个分支，`interrupt()` 会冻结整个图** —— 确认首章细纲时，
正在跑的骨架/细纲生产会被一起冻住，用户要的并行就没了。

因此本模块每个图都是**短的、单职责的**，由 `orchestrator.py`（纯 Python）
调度。每图一个 `thread_id` → 崩溃恢复是**单元级**的。

## 四条 LangGraph 硬约束（调研实测，逐条落地）

| 约束 | issue | 本模块的做法 |
|---|---|---|
| 节点不得原地修改 state | `#8314` | 节点只 `return {...}`，返回新字典 |
| 并行分支不得写同一状态键 | `#8115` | 本模块的图**无并行分支**（并发在 orchestrator 层） |
| 时间存 UTC ISO8601 字符串 | `#8826` | state 里不放 datetime |
| >180s 调用被静默重放 | `#7417` | 每次 `ainvoke` 用**幂等键**做 `thread_id` |

## state 只放引用

`graph/state.py` 的约定：只有 `*_ref` / `*_hash` / 小标量。
正文与细纲 JSON 一律外置到 `ArtifactStore`（契约规则 5/19 强制）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from storyforge.config import get_settings
from storyforge.graph.state import (
    BibleState,
    BookState,
    DetailState,
    SkeletonState,
    VolumePlanState,
)
from storyforge.infra.llm.client import LLMClient
from storyforge.infra.storage.artifacts import ArtifactStore
from storyforge.graph.nodes.bible_architect import (
    build_bible,
    persist_bible,
)
from storyforge.graph.nodes.detail_planner import (
    build_detail_batch,
    persist_plan,
)
from storyforge.graph.nodes.skeleton_planner import (
    build_skeleton,
    persist_skeleton,
)
from storyforge.graph.nodes.volume_planner import (
    load_series_plan,
    persist_series_plan,
    plan_volumes,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# checkpoint：一库一连接，thread_id = 幂等键
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def open_checkpointer(db_path: str | None = None) -> AsyncIterator[AsyncSqliteSaver]:
    """打开 checkpoint 存储（SQLite + WAL）。

    ★ 用 `thread_id = 幂等键`，使崩溃恢复后**已完成单元不重跑**（不变量 4），
    同时配合 `LLMClient` 的幂等存储防 `#7417` 的静默重放重复扣费。
    """
    settings = get_settings()
    path = db_path or str(settings.checkpoint_db_path)
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        yield saver


def thread_config(idempotency_key: str) -> dict[str, Any]:
    """由幂等键派生 LangGraph 配置。

    ★ `recursion_limit` 必须显式设定：默认 25 步，而本项目的图都是**短图**
    （≤4 节点），一旦出现意外的重试循环，需要显式失败而不是静默跑到上限。
    """
    return {
        "configurable": {"thread_id": idempotency_key},
        "recursion_limit": 30,
    }

# --------------------------------------------------------------------------- #
# 依赖容器
# --------------------------------------------------------------------------- #


class GraphDeps:
    """图执行所需的依赖（**显式注入**，不从全局取）。

    旧项目的问题是 `pipeline.py` 直接在模块里 `from ..db import ...`，
    导致**无法在测试中替换**（121/123 个测试文件被迫改 `os.environ`）。

    这里由调用方（orchestrator 的执行器）构造并传入。
    """

    def __init__(self, *, client: LLMClient, store: ArtifactStore) -> None:
        self.client = client
        self.store = store


# --------------------------------------------------------------------------- #
# 图 1：圣经（C-1）
# --------------------------------------------------------------------------- #


def build_bible_graph(deps: GraphDeps) -> CompiledStateGraph:
    """圣经生成图 —— 单节点（生成 + 校验 + 落盘）。"""

    async def generate(state: BibleState) -> dict[str, Any]:
        result = await build_bible(
            deps.client,
            book_id=int(state["book_id"]),
            topic=state.get("topic", ""),
            genre=state.get("genre", ""),
            idempotency_key=f"bible:{state['book_id']}",
        )
        if not result.ok or result.bible is None:
            return {
                "ok": False,
                "violations": result.violations or [result.error or "未知失败"],
            }
        ref, _h = persist_bible(deps.store, int(state["book_id"]), result.bible)
        return {
            "ok": True,
            "violations": [],
            "refs": {**state.get("refs", {}), "bible_ref": ref},
        }

    g = StateGraph(BibleState)
    g.add_node("generate", generate)
    g.add_edge(START, "generate")
    g.add_edge("generate", END)
    return g.compile(name="bible")


# --------------------------------------------------------------------------- #
# 图 2：全卷升级台阶（C-4，对策②核心）
# --------------------------------------------------------------------------- #


def build_volume_plan_graph(deps: GraphDeps) -> CompiledStateGraph:
    """全卷台阶图 —— 单节点（规划 + 结构闸门 + 增量补缺 + 落盘）。"""

    async def plan(state: VolumePlanState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        bible_ref = state.get("refs", {}).get("bible_ref", "")
        bible_text = deps.store.read_text(bible_ref) if bible_ref else ""
        result = await plan_volumes(
            deps.client,
            book_id=book_id,
            topic=state.get("topic", ""),
            genre=state.get("genre", ""),
            target_chapters=int(state.get("target_chapters", 0)),
            bible_text=bible_text,
            idempotency_key=f"volume_plan:{book_id}",
        )
        if not result.ok or result.plan is None:
            return {
                "gate_ok": False,
                "gate_issues": [i.to_dict() for i in result.gate.issues],
                "repair_round": result.repair_rounds,
            }
        ref = persist_series_plan(deps.store, result.plan)
        return {
            "gate_ok": True,
            "gate_issues": [],
            "repair_round": result.repair_rounds,
            "total_volumes": result.plan.total_volumes,
            "series_plan_ref": ref,
            "refs": {**state.get("refs", {}), "series_plan_ref": ref},
            # ★ C-4：全卷台阶必须一次定稿，等人工确认后再开工骨架
            "awaiting_confirm": "C4_volume_ladder",
        }

    g = StateGraph(VolumePlanState)
    g.add_node("plan", plan)
    g.add_edge(START, "plan")
    g.add_edge("plan", END)
    return g.compile(name="volume_plan")


# --------------------------------------------------------------------------- #
# 图 3：单卷骨架（C-2）
# --------------------------------------------------------------------------- #


def build_skeleton_graph(deps: GraphDeps) -> CompiledStateGraph:
    """骨架图 —— 单卷一次调用（生成 + 校验 + 落盘）。

    ★ 骨架**不做分批**：一卷的骨架是一个整体（线之间互相约束），
    切片会破坏「段落目标两两不同」这类全局约束。
    分批的是**细纲**（`pipeline.detail_batch_size`）。
    """

    async def plan(state: SkeletonState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        volume_no = int(state.get("current_volume", 1))
        refs = state.get("refs", {})
        series = load_series_plan(deps.store, book_id)
        volume = next(
            (v for v in series.volumes if v.volume_no == volume_no), None
        )
        if volume is None:
            return {
                "violations": [f"全卷台阶里没有第 {volume_no} 卷（不可静默跳过）"]
            }
        bible_text = deps.store.read_text(refs.get("bible_ref", ""))
        result = await build_skeleton(
            deps.client,
            book_id=book_id,
            volume=volume,
            bible_text=bible_text,
            idempotency_key=f"skeleton:{book_id}:v{volume_no}",
        )
        if not result.ok or result.skeleton is None:
            return {"violations": result.violations or [result.error or "未知失败"]}
        ref = persist_skeleton(deps.store, book_id, volume_no, result.skeleton)
        out: dict[str, Any] = {
            "violations": [],
            "skeleton_ref": ref,
            "refs": {**refs, "skeleton_ref": ref},
        }
        # ★ C-2 只在**首批**骨架后触发（决策 D-14：确认后后续批次方向正确，
        # 且细纲可立即并行开工，不阻塞）
        if int(state.get("batch_no", 1)) <= 1:
            out["awaiting_confirm"] = "C2_skeleton_first_batch"
        return out

    g = StateGraph(SkeletonState)
    g.add_node("plan", plan)
    g.add_edge(START, "plan")
    g.add_edge("plan", END)
    return g.compile(name="skeleton")


# --------------------------------------------------------------------------- #
# 图 4：细纲批次（C-3）
# --------------------------------------------------------------------------- #


def build_detail_batch_graph(deps: GraphDeps) -> CompiledStateGraph:
    """细纲批次图 —— 一批一次调用（生成 + 注入伤势节拍 + lint + 落盘）。

    ★ 本图是**恢复率 0%** 的修复路径的最后一环：
    `build_detail_batch` 内部把 `HealthScheduler` 的节拍注入每章 `health_beats`。
    """

    async def plan(state: DetailState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        volume_no = int(state.get("current_volume", 1))
        refs = state.get("refs", {})
        series = load_series_plan(deps.store, book_id)
        volume = next(
            (v for v in series.volumes if v.volume_no == volume_no), None
        )
        if volume is None:
            return {"lint_issues": [f"全卷台阶里没有第 {volume_no} 卷"]}

        bible_text = deps.store.read_text(refs.get("bible_ref", ""))
        result = await build_detail_batch(
            deps.client,
            book_id=book_id,
            volume=volume,
            bible_text=bible_text,
            batch_start=int(state["batch_start"]),
            batch_end=int(state["batch_end"]),
            idempotency_key=(
                f"detail:{book_id}:v{volume_no}:"
                f"{state['batch_start']}-{state['batch_end']}"
            ),
        )
        if not result.ok:
            # ★ 失败批次**不落盘**：半成品落盘会被下游当成已就绪
            # （旧项目"写出的信号无读者"与"缺省即通过"的共同形态）
            return {
                "lint_issues": [
                    f"第{n}章：{i}"
                    for n, issues in sorted(result.lint_issues.items())
                    for i in issues
                ],
            }
        new_refs = [
            persist_plan(deps.store, book_id, result.plans[n])
            for n in sorted(result.plans)
        ]
        out: dict[str, Any] = {
            "lint_issues": [],
            "detail_refs": new_refs,
            "health_beats_injected": result.health_beats_injected,
        }
        # ★ C-3：**第一章**细纲就绪即触发（不是首批全部完成）——
        # 这样正文可以立刻开工，与后续细纲并行（不变量 2）
        if int(state["batch_start"]) <= 1 <= int(state["batch_end"]):
            out["awaiting_confirm"] = "C3_first_detail"
        return out

    g = StateGraph(DetailState)
    g.add_node("plan", plan)
    g.add_edge(START, "plan")
    g.add_edge("plan", END)
    return g.compile(name="detail_batch")


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #


def build_all(deps: GraphDeps) -> dict[str, CompiledStateGraph]:
    """构造全部图（供 orchestrator 的执行器按 stage 取用）。"""
    return {
        "bible": build_bible_graph(deps),
        "volume_plan": build_volume_plan_graph(deps),
        "skeleton": build_skeleton_graph(deps),
        "detail": build_detail_batch_graph(deps),
    }


__all__ = [
    "open_checkpointer",
    "thread_config",
    "GraphDeps",
    "build_bible_graph",
    "build_volume_plan_graph",
    "build_skeleton_graph",
    "build_detail_batch_graph",
    "build_all",
    "BookState",
]
