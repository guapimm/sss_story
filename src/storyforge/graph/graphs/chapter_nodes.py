"""单章闭环图的**节点实现**（P4-11）。

## 为什么从 `chapter.py` 拆出来

契约规则 10 把 `build_chapter_graph` 拦在了 **321 行**（单函数上限 200，
**该上限仍然有效**）。这不是形式主义：旧项目 `_commit_chapter` 就是 **1,239 行**，
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
        entities: Any | None = None,
        relations: Any | None = None,
        checkpointer: Any = None,
    ) -> None:
        self.client = client
        self.store = store
        self.thresholds = thresholds
        #: ★ 仓储（可选）。为 None 时提交节点**不入库**，但会如实说明。
        #:
        #: 可选而非必填的原因与 `books.py:ChapterDeps.injuries` 完全一致：
        #: 纯文件模式的测试与产物回放不该被迫建库。
        #: 但**生产链路必须传** —— 否则实体/关系链路又断在这里
        #: （这正是本轮修的缺口：抽取跑了、产物写了、**库里一行没有**）。
        self.entities = entities
        self.relations = relations
        #: ★ checkpoint 存储（可选）。为 None 时本章**无恢复能力**（旧行为）。
        #: 与 `GraphDeps.checkpointer` 同一纪律：生产必须传，否则
        #: "崩溃恢复后已完成单元不重跑"（不变量 4）不成立。
        self.checkpointer = checkpointer


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


def _known_from_db(deps: ChapterDeps, book_id: int) -> list[tuple[int, str, list[str]]]:
    """从库里取既有实体，喂给 `resolve_entities`（`known_entities` 的真来源）。

    ## 为什么必须有它

    抽取节点读 `state.get("known_entities", [])` —— 而本图的 State
    **从来没人写过这个键**（`chapter.py` 的初值里没有）。于是它恒为空列表，
    后果是每章对模型而言都是"第一次见这个名字"：
    模型反复给出新规范名 → 反复新建 → 旧项目
    「同一实体两个名字被建成两行」的复发。

    入库失败**不阻断抽取**（名字归一降级为"全新"而已），但要留痕。
    """
    if deps.entities is None:
        return []
    try:
        return deps.entities.all_names(book_id)
    except Exception:  # noqa: BLE001
        logger.warning("读既有实体失败：book=%d（本章按全新实体处理）",
                       book_id, exc_info=True)
        return []


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
        #: 提及名 → 类型（来自 scene 抽取）。`EntityResolutionOut` 不带类型，
        #: 落库时要用它 —— 否则所有实体都会落成兜底类型，
        #: "势力实体 0 个"这类聚合统计又会失真。
        mention_kinds = {
            e.name: getattr(e, "kind", "") for e in (scene.entities if scene else [])
        }
        entities = None
        if mentions:
            # ★ 既有实体从**库里**读，而不是只读 state。
            #   state 里的 `known_entities` 在本图内**从来没人写**
            #   （`chapter.py` 的 State 初值里没有它），于是每章对模型
            #   而言都是"第一次见这个名字" → 反复新建同一实体。
            known = list(state.get("known_entities", []))
            if not known and deps.entities is not None:
                known = _known_from_db(deps, book_id)
            entities, c2 = await resolve_entities(
                deps.client, book_id=book_id, global_no=global_no,
                mentions=mentions, known_entities=known,
                idempotency_key=f"chapter:resolve:{book_id}:{global_no}",
            )
            calls += c2
            if entities is not None:
                # 供 commit 落库时取类型（不改 schema，就近挂载）
                try:
                    entities.mention_kinds = mention_kinds  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001, S110
                    pass

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
            "mention_kinds": mention_kinds,
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
    """⑦ 提交 —— 产物完整性校验 + **实体/关系落库**。

    ## ★ 本节点补的"最后一公里"

    在此之前：抽取节点每章都抽实体与关系、也写了
    `artifacts/extractions/{章}.json`，但 `entities` /
    `entity_relations` **两张表零写入** —— `EntityRepository`
    全仓无生产调用点。本节点接通它。

    入库失败**不判整章失败**（产物已落盘，与
    `books.py:_mark_heal_planned` 同一条纪律），但**必须留痕**：
    `commit_note` 会说明入库结果，`persist_problems` 带出问题清单。
    「缺省即不通过」—— 没入库就不报"已提交到账本"。

    ## 没有仓储时（`deps.entities is None`）

    如实说明"未入库"，**不假装**。纯文件模式（测试/回放）走这条。
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

        out: dict[str, Any] = {
            "committed": True,
            "status": "committed",
            "prose_chars": len(prose.replace(" ", "")),
        }

        note = persist_chapter_memory(
            deps, book_id=book_id, global_no=global_no,
            extraction_ref=str(state["extraction_ref"]), out=out,
        )
        out["commit_note"] = note
        return out

    return commit


def persist_chapter_memory(
    deps: ChapterDeps,
    *,
    book_id: int,
    global_no: int,
    extraction_ref: str,
    out: dict[str, Any],
) -> str:
    """把本章抽取产物写进库，并把结果写回 `out`（供 state 使用）。

    ★ 抽成独立函数（而非塞进 `commit` 闭包）的理由：它需要被单测
    直接调用（闭包只能通过跑图触发，代价高且失败信息被图吞掉）。
    """
    from storyforge.graph.nodes.persist import persist_extraction

    if deps.entities is None or deps.relations is None:
        # 不假装 —— 明确说明为什么没入库
        return "产物已落盘；未注入仓储（纯文件模式），DB 未更新"

    try:
        payload = deps.store.read_json(extraction_ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读抽取产物失败：book=%d 章=%d", book_id, global_no, exc_info=True)
        out["persist_problems"] = [f"读抽取产物失败：{exc}"]
        return "入库失败（抽取产物不可读），已留痕"

    from storyforge.infra.llm.extraction_schemas import (
        EntityResolutionOut,
        RelationExtractionOut,
    )

    def _parse(model, key):
        raw = (payload or {}).get(key)
        if not isinstance(raw, dict):
            return None
        try:
            return model.model_validate(raw)
        except Exception:  # noqa: BLE001
            logger.warning("抽取产物 %s 解析失败：book=%d 章=%d",
                           key, book_id, global_no, exc_info=True)
            return None

    entities = _parse(EntityResolutionOut, "entities")
    relations = _parse(RelationExtractionOut, "relations")
    raw_kinds = (payload or {}).get("mention_kinds")
    mention_kinds = raw_kinds if isinstance(raw_kinds, dict) else None

    try:
        res = persist_extraction(
            entities=entities, relations=relations,
            book_id=book_id, chapter_no=global_no,
            entity_repo=deps.entities, relation_repo=deps.relations,
            mention_kinds=mention_kinds,
        )
    except Exception as exc:  # noqa: BLE001
        # 入库失败不阻断提交（产物在），但要留痕
        logger.warning("实体/关系入库失败：book=%d 章=%d", book_id, global_no,
                       exc_info=True)
        out["persist_problems"] = [f"入库异常：{type(exc).__name__}: {exc}"]
        return f"入库失败（{type(exc).__name__}），产物在盘上"

    out["persisted"] = True
    out["persist_summary"] = res.summary()
    if res.problems:
        out["persist_problems"] = res.problems
    return f"已入库：{res.summary()}"


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
