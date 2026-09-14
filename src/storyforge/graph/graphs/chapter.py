"""单章闭环图（P4-11）—— **只负责接线**。

## 流程

    就绪闸门 → 写作 → 打分 → 路由 →（按需）审校 → 定向重写
      → 抽取×3 → 提交

节点实现见 `chapter_nodes.py`（拆开的理由：契约规则 10 把本函数拦在
321 行，而上限是 200 —— 旧项目 `_commit_chapter` 就是 1,239 行）。

## ★ 就绪闸门（P4-2）：**干净退出**，不是报错

细纲未就绪时本章**不得**开写（正文最多领先细纲 3 章，决策 D-13）。
但"未就绪"是**正常的调度状态**（细纲 worker 还在跑），不是失败 ——
闸门返回 `status="not_ready"` 让协调器稍后重排，
而不是抛异常把该阶段的 worker 打成 FAILED。

旧项目对照：预生成线程在细纲未就绪时**空转 6 秒轮询且不留痕**
（书 23：大纲完成 23:00:44 → 首章文件 mtime 23:07:19，晚 7 分钟）。

## ★ 审校路由（P4-6）

    无条件审：黄金三章（1/2/3）/ 卷末 / 高潮·Boss 章（D-14 C-5）
    阈值触发：打分卡 hard 层命中（少而准，见 scorecard/aggregate.py）

旧项目实测 `review_config.json`：standard 档**只审头 5 章 + 尾 7 章**
→ 100 章的书 **88 章完全不审**。

## ★ C-7 人工确认（P4-13）

同一章重写 `max_rewrites` 次仍不达标 → 暂停等人工，**不无限烧 token**。
由 `needs_c7_confirm`（纯函数）判定，便于单测。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from storyforge.config import get_settings
from storyforge.domain.enums import ConfirmPoint
from storyforge.graph.state import ChapterState
from storyforge.graph.graphs.chapter_nodes import (
    BLOCKED,
    NOT_READY,
    READY,
    ChapterDeps,
    make_commit_node,
    make_extract_node,
    make_readiness_node,
    make_review_node,
    make_revise_node,
    make_score_node,
    make_write_node,
)

#: 确认点标识（与 `graph/graphs/executor.py` 的翻译表一致）
C7_CONFIRM = ConfirmPoint.C7_QUALITY_GATE.value


def needs_c7_confirm(state: dict[str, Any], *, max_rewrites: int | None = None) -> bool:
    """是否触发 C-7（质量闸门连续失败 → 暂停等人工）。

    条件（任一）：
      · 重写次数已达上限且仍未过闸门
      · 写作本身失败（拒答/空输出）

    ★ 存在的意义是**止损**：旧项目没有这个闸门，
    重写空转的成本无人统计（`no_improve:6->6` 反复出现）。
    """
    limit = max_rewrites if max_rewrites is not None else get_settings().scorecard.max_rewrites
    status = state.get("status", "")
    if status == "write_failed":
        return True
    if int(state.get("rewrite_count", 0)) >= limit and state.get("needs_review"):
        return True
    return False


def build_chapter_graph(deps: ChapterDeps) -> CompiledStateGraph:
    """组装单章闭环图。

    路由（全部由 state 里的小标量决定，便于单测）：
      `readiness` 非 ready            → END（干净退出，不是失败）
      `write` 失败                    → END（带 error）
      打分不需审                       → 直接抽取
      审校未产出可用修订               → 直接抽取
      重写后仍不达标且达上限           → END（触发 C-7）
    """

    def after_readiness(state: dict[str, Any]) -> str:
        return "write" if state.get("status") == READY else "stop"

    def after_write(state: dict[str, Any]) -> str:
        return "score" if state.get("status") == "written" else "stop"

    def after_score(state: dict[str, Any]) -> str:
        return "review" if state.get("needs_review") else "extract"

    def after_review(state: dict[str, Any]) -> str:
        return "revise" if state.get("review_ref") else "extract"

    def after_revise(state: dict[str, Any]) -> str:
        if needs_c7_confirm(state):
            return "stop"
        return "extract"

    g = StateGraph(ChapterState)
    g.add_node("readiness", make_readiness_node(deps))
    g.add_node("write", make_write_node(deps))
    g.add_node("score", make_score_node(deps))
    g.add_node("review", make_review_node(deps))
    g.add_node("revise", make_revise_node(deps))
    g.add_node("extract", make_extract_node(deps))
    g.add_node("commit", make_commit_node(deps))

    g.add_edge(START, "readiness")
    g.add_conditional_edges(
        "readiness", after_readiness, {"write": "write", "stop": END}
    )
    g.add_conditional_edges("write", after_write, {"score": "score", "stop": END})
    g.add_conditional_edges(
        "score", after_score, {"review": "review", "extract": "extract"}
    )
    g.add_conditional_edges(
        "review", after_review, {"revise": "revise", "extract": "extract"}
    )
    g.add_conditional_edges(
        "revise", after_revise, {"extract": "extract", "stop": END}
    )
    g.add_edge("extract", "commit")
    g.add_edge("commit", END)
    return g.compile(name="chapter")


__all__ = [
    "READY",
    "NOT_READY",
    "BLOCKED",
    "C7_CONFIRM",
    "ChapterDeps",
    "needs_c7_confirm",
    "build_chapter_graph",
]
