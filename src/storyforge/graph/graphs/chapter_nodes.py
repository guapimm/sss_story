"""单章闭环图的**节点实现**（P4-11）。

## 为什么从 `chapter.py` 拆出来

契约规则 10 把 `build_chapter_graph` 拦在了 **321 行**（上限 200）。
这不是形式主义：旧项目 `_commit_chapter` 就是 **1,239 行**，
项目自己的 `ARCHITECTURE_MAP.md` 把它列为结构债。

拆法：每个节点一个**工厂函数**（`make_xxx_node(deps) -> node`），
图的组装只负责接线。这样：
  · 每个节点可**单独测试**（不必跑整图）
  · 节点之间的依赖通过 `deps` 显式传递，没有隐式全局状态
  · 加节点不会让 `build_chapter_graph` 变长

## 契约约束（每个节点都必须遵守）

· 只 `return {...}`，**不原地改 state**（`langgraph#8314` 静默数据损坏）
· state 只放 ref / hash / 小标量 / 有界上下文块（规则 5/19）
· 每次 LLM 调用都有幂等键（`langgraph#7417` >180s 静默重放）
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from storyforge.config import get_settings
from storyforge.graph.state import ChapterState
from storyforge.infra.storage.artifacts import ArtifactStore, content_hash
from storyforge.scorecard.aggregate import Scorecard, evaluate

logger = logging.getLogger(__name__)

#: 就绪闸门的三种结论
READY = "ready"
NOT_READY = "not_ready"
BLOCKED = "blocked"

NodeFn = Callable[[ChapterState], Any]


class ChapterDeps:
    """单章闭环的依赖（**显式注入**，便于测试替换）。"""

    def __init__(
        self,
        *,
        client: Any,
        store: ArtifactStore,
        thresholds: Any | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.thresholds = thresholds


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def check_readiness(
    *,
    plan: Any | None,
    plan_ref: str,
    global_no: int,
    detail_ready_through: int,
    lead_limit: int,
) -> tuple[str, str]:
    """就绪闸门（P4-2）。返回 `(status, reason)`。

    ★ 三种结论**必须区分**：
      · `ready`     —— 细纲在位，可以开写
      · `not_ready` —— 细纲还没轮到本章（**正常调度态**，稍后重排）
      · `blocked`   —— 细纲缺失但**已经该有了**（真问题，要留痕）

    旧项目对照：预生成线程在细纲未就绪时**空转 6 秒轮询且不留痕**
    （书 23：大纲完成 23:00:44 → 首章文件 mtime 23:07:19，晚 7 分钟）。

    `plan` 是**权威**（由调用方从产物读出）；`plan_ref` 只用于在
    "该有却没有"时给出定位信息。
    """
    if plan is None:
        if global_no <= detail_ready_through:
            where = f"（期望产物：{plan_ref}）" if plan_ref else ""
            return BLOCKED, (
                f"第{global_no}章在细纲覆盖范围（≤{detail_ready_through}）内，"
                f"但找不到细纲产物{where} —— 细纲环节可能失败或被跳过"
            )
        return NOT_READY, (
            f"第{global_no}章细纲尚未就绪（当前细纲覆盖到第 {detail_ready_through} 章）"
        )
    if global_no > detail_ready_through + lead_limit:
        return NOT_READY, (
            f"第{global_no}章领先细纲 {global_no - detail_ready_through} 章，"
            f"超过上限 {lead_limit}（正文跑太快会让后续细纲全废）"
        )
    return READY, ""


def should_review(card: Scorecard, *, is_volume_end: bool = False) -> tuple[bool, str]:
    """路由是否送审（P4-6）。返回 `(是否, 理由)`。"""
    if card.forced_review:
        reasons = "、".join(t.value for t in card.triggers) or "无条件审"
        return True, f"无条件送审（{reasons}）"
    if card.needs_review:
        reasons = "、".join(t.value for t in card.triggers)
        return True, f"打分卡触发：{reasons}"
    return False, f"打分卡通过（{card.score:.3f}）"


def _prev_texts(store: ArtifactStore, book_id: int, global_no: int) -> list[str]:
    """取前若干章正文（跨章重叠率的分母）。缺章跳过，不报错。"""
    window = get_settings().scorecard.cross_window
    out: list[str] = []
    for no in range(max(1, global_no - window), global_no):
        try:
            out.append(store.read_text(ArtifactStore.prose_key(book_id, no)))
        except Exception:  # noqa: BLE001 - 从中间开始写时缺前置章是正常的
            continue
    return out


def _score_now(
    deps: ChapterDeps, book_id: int, global_no: int, prose: str, plan: Any, state: ChapterState
) -> Scorecard:
    return evaluate(
        prose, global_no=global_no, plan=plan,
        prev_texts=_prev_texts(deps.store, book_id, global_no) or None,
        beat_type=plan.beat_type.value,
        is_volume_end=bool(state.get("is_volume_end", False)),
        thresholds=deps.thresholds,
    )


# --------------------------------------------------------------------------- #
# 节点工厂
# --------------------------------------------------------------------------- #


def make_readiness_node(deps: ChapterDeps) -> NodeFn:
    """① 就绪闸门 —— 细纲未就绪则**干净退出**，不是报错。"""
    from storyforge.graph.nodes.detail_planner import load_plan  # noqa: PLC0415

    async def readiness(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        try:
            plan = load_plan(deps.store, book_id, global_no)
        except Exception:  # noqa: BLE001 - 细纲不存在是预期情况
            plan = None

        status, reason = check_readiness(
            plan=plan, plan_ref=state.get("plan_ref", ""), global_no=global_no,
            detail_ready_through=int(state.get("detail_ready_through", 0)),
            lead_limit=get_settings().pipeline.chapter_lead_limit,
        )
        if status != READY:
            return {"status": status, "error": reason}
        return {"status": READY, "error": ""}

    return readiness


def make_write_node(deps: ChapterDeps) -> NodeFn:
    """② 写作 —— 三层 prompt + 长度闭环 + 拒答检测。"""
    from storyforge.graph.nodes.chapter_writer import (  # noqa: PLC0415
        persist_prose,
        write_chapter,
    )
    from storyforge.graph.nodes.detail_planner import load_plan  # noqa: PLC0415

    async def write(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        plan = load_plan(deps.store, book_id, global_no)
        refs = state.get("refs", {})
        bible_text = deps.store.read_text(refs.get("bible_ref", ""))

        result = await write_chapter(
            deps.client, book_id=book_id, plan=plan, bible_text=bible_text,
            volume_block_text=state.get("volume_block", ""),
            segment_block_text=state.get("segment_block", ""),
            states=state.get("states_block", ""),
            injuries=state.get("injuries_block", ""),
            timeline=state.get("timeline_block", ""),
            previous_tail=state.get("previous_tail", ""),
            style_rules=state.get("style_rules", ""),
            idempotency_key=f"chapter:write:{book_id}:{global_no}",
        )
        if not result.ok:
            return {
                "status": "write_failed",
                "error": result.error or "写作失败",
                "needs_review": True,
            }
        ref = persist_prose(deps.store, book_id, global_no, result.prose)
        out: dict[str, Any] = {
            "prose_ref": ref,
            "prose_hash": result.prose_hash,
            "prose_chars": result.chars,
            "status": "written",
        }
        if result.problems:
            out["write_problems"] = list(result.problems)
        return out

    return write


def make_score_node(deps: ChapterDeps) -> NodeFn:
    """③ 打分 + 连续性 —— **零 LLM**、可复现。"""
    from storyforge.graph.nodes.chapter_writer import load_prose  # noqa: PLC0415
    from storyforge.graph.nodes.detail_planner import load_plan  # noqa: PLC0415

    async def score(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        prose = load_prose(deps.store, book_id, global_no)
        plan = load_plan(deps.store, book_id, global_no)
        card = _score_now(deps, book_id, global_no, prose, plan, state)
        needs, reason = should_review(card)
        return {
            "scorecard": card.to_dict(),
            "needs_review": needs,
            "review_reason": reason,
            "status": "scored",
        }

    return score


def make_review_node(deps: ChapterDeps) -> NodeFn:
    """④ 审校 —— issues 非空则必须给修订稿（schema 层强制）。"""
    from storyforge.graph.nodes.chapter_writer import load_prose  # noqa: PLC0415
    from storyforge.graph.nodes.detail_planner import load_plan  # noqa: PLC0415
    from storyforge.graph.nodes.reviewer import review_chapter  # noqa: PLC0415

    async def review(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        prose = load_prose(deps.store, book_id, global_no)
        plan = load_plan(deps.store, book_id, global_no)

        result, calls = await review_chapter(
            deps.client, book_id=book_id, global_no=global_no, prose=prose,
            plan_detail=state.get("plan_detail", ""),
            states=state.get("states_block", ""),
            end_hook=plan.end_hook,
            is_transition=plan.is_transition,
            idempotency_key=(
                f"chapter:review:{book_id}:{global_no}:"
                f"{state.get('rewrite_count', 0)}"
            ),
        )
        ref = ArtifactStore.review_key(book_id, global_no)
        if result is not None:
            deps.store.write_json(ref, result.model_dump())
        return {
            "review_ref": ref.key if result is not None else "",
            "review_score": result.score if result else 0.0,
            "review_issues": len(result.issues) if result else 0,
            "review_calls": calls,
            "status": "reviewed",
        }

    return review


def make_revise_node(deps: ChapterDeps) -> NodeFn:
    """⑤ 定向重写 + **复检**（修订必须过闸门才算数）。"""
    from storyforge.graph.nodes.chapter_writer import load_prose  # noqa: PLC0415
    from storyforge.graph.nodes.detail_planner import load_plan  # noqa: PLC0415
    from storyforge.graph.nodes.reviewer import (  # noqa: PLC0415
        apply_revision_if_better,
        revise_chapter,
    )
    from storyforge.infra.llm.schemas import ReviewResultOut  # noqa: PLC0415

    async def revise(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        review_ref = state.get("review_ref", "")
        if not review_ref:
            return {"status": "no_review", "revision_applied": False}

        prose = load_prose(deps.store, book_id, global_no)
        plan = load_plan(deps.store, book_id, global_no)
        review = ReviewResultOut.model_validate(deps.store.read_json(review_ref))
        if not review.issues or not review.has_usable_revision:
            return {
                "status": "no_revision_needed",
                "revision_applied": False,
                "revision_note": "审校未给出可用修订稿",
            }

        revised, calls = await revise_chapter(
            deps.client, book_id=book_id, global_no=global_no, prose=prose,
            review=review,
            idempotency_key=(
                f"chapter:revise:{book_id}:{global_no}:"
                f"{state.get('rewrite_count', 0)}"
            ),
        )
        outcome = apply_revision_if_better(
            prose, revised, global_no=global_no, plan=plan,
            prev_texts=_prev_texts(deps.store, book_id, global_no) or None,
            beat_type=plan.beat_type.value,
            is_volume_end=bool(state.get("is_volume_end", False)),
        )
        if not outcome.revision_applied:
            return {
                "revision_applied": False,
                "revision_note": outcome.decision_note,
                "revision_calls": calls,
                "status": "revision_rejected",
            }

        ArtifactStore(deps.store.root).write_text(
            ArtifactStore.prose_key(book_id, global_no), outcome.final_prose
        )
        card = _score_now(deps, book_id, global_no, outcome.final_prose, plan, state)
        return {
            "prose_hash": content_hash(outcome.final_prose),
            "prose_chars": len(outcome.final_prose.replace(" ", "")),
            "scorecard": card.to_dict(),
            "needs_review": card.needs_review,
            "revision_applied": True,
            "revision_note": outcome.decision_note,
            "revision_calls": calls,
            "status": "revised",
        }

    return revise


def make_extract_node(deps: ChapterDeps) -> NodeFn:
    """⑥ 抽取 ×3 —— 每个节点只抽一类（对策⑥）。"""
    from storyforge.graph.nodes.chapter_writer import load_prose  # noqa: PLC0415
    from storyforge.graph.nodes.extractors import (  # noqa: PLC0415
        extract_relations,
        extract_scene,
        resolve_entities,
        validate_relations,
        validate_scene,
    )

    async def extract(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        prose = load_prose(deps.store, book_id, global_no)
        calls = 0
        problems: list[str] = []

        scene, c1 = await extract_scene(
            deps.client, book_id=book_id, global_no=global_no, prose=prose,
            idempotency_key=f"chapter:extract_scene:{book_id}:{global_no}",
        )
        calls += c1
        if scene is not None:
            problems += validate_scene(scene)

        mentions = [e.name for e in (scene.entities if scene else [])]
        entities = None
        if mentions:
            entities, c2 = await resolve_entities(
                deps.client, book_id=book_id, global_no=global_no,
                mentions=mentions,
                known_entities=list(state.get("known_entities", [])),
                idempotency_key=f"chapter:resolve:{book_id}:{global_no}",
            )
            calls += c2

        relations, c3 = await extract_relations(
            deps.client, book_id=book_id, global_no=global_no, prose=prose,
            idempotency_key=f"chapter:extract_relations:{book_id}:{global_no}",
        )
        calls += c3
        if relations is not None:
            problems += validate_relations(relations)

        ref = ArtifactStore.extraction_key(book_id, global_no)
        deps.store.write_json(ref, {
            "scene": scene.model_dump() if scene else None,
            "entities": entities.model_dump() if entities else None,
            "relations": relations.model_dump() if relations else None,
            "problems": problems,
        })
        out: dict[str, Any] = {
            "extraction_ref": ref.key,
            "extraction_calls": calls,
            "status": "extracted",
        }
        if problems:
            out["extraction_problems"] = problems
        return out

    return extract


def make_commit_node(deps: ChapterDeps) -> NodeFn:
    """⑦ 提交 —— **产物完整性校验**；DB 入库待仓储层实现。

    ★ 本节点在 P4 阶段只校验产物并写结论，**不假装已入库**：
    `infra/storage/repositories/` 尚未实现，因此 `commit_note` 显式说明。
    「缺省即不通过」—— 没有入库就不报成功。
    """
    from storyforge.graph.nodes.chapter_writer import load_prose  # noqa: PLC0415

    async def commit(state: ChapterState) -> dict[str, Any]:
        book_id = int(state["book_id"])
        global_no = int(state["global_no"])
        try:
            prose = load_prose(deps.store, book_id, global_no)
        except Exception as exc:  # noqa: BLE001
            return {
                "committed": False, "status": "commit_failed",
                "error": f"正文缺失，拒绝提交：{exc}",
            }
        if not state.get("extraction_ref"):
            return {
                "committed": False, "status": "commit_failed",
                "error": "缺抽取产物，拒绝提交（记忆账本会与正文脱节）",
            }
        return {
            "committed": True,
            "status": "committed",
            "prose_chars": len(prose.replace(" ", "")),
            "commit_note": "产物已落盘；DB 入库待 infra/storage/repositories 实现",
        }

    return commit


__all__ = [
    "READY",
    "NOT_READY",
    "BLOCKED",
    "NodeFn",
    "ChapterDeps",
    "check_readiness",
    "should_review",
    "make_readiness_node",
    "make_write_node",
    "make_score_node",
    "make_review_node",
    "make_revise_node",
    "make_extract_node",
    "make_commit_node",
]
