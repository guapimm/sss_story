"""LangGraph 图状态定义（决策 D-11 + D-13）。

## ★ 核心约束：State 只放**引用**，不放正文

LangGraph checkpoint 会把每次节点执行的 state 完整序列化。
官方承认 200 轮可达 **5.3 GB**，社区实测 32 线程 **1.24 GB**。

旧项目的同构问题：把 400 章 `outline` 塞进 `books.outline` 单列（书 23 实测）。

因此本模块的 state **只有**：
  · ID / 序号（轻量）
  · `*_ref`（产物在文件或 DB 里的定位符）
  · `*_hash`（内容指纹，用于缓存与一致性校验）
  · 小结果（打分卡、违规列表、重试计数）

**禁止**：`content` / `prose` / `bible` / `outline_json` 这类大文本字段。
由 `tests/contract/test_architecture_invariants.py` 规则 5/19 强制。

## 其余三条 LangGraph 硬约束（调研实测）

| 约束 | 官方 issue | 后果 |
|---|---|---|
| 节点**不得原地修改** state | `#8314` 状态通道引用透传 | 污染调用方输入（**静默数据损坏**） |
| 并行分支**不得写同一状态键** | `#8115` 并行写竞态 | **静默丢 checkpoint**（30/30 复现） |
| 时间存 **UTC ISO8601 字符串** | `#8826` datetime 静默丢时区 | 时间戳不可靠 |

## 章号

`global_no` 是**唯一**章号权威（决策 D-15）。
卷内序号 = `global_no - volume.chapter_start + 1`，**派生不存储**。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #


class GraphRefs(TypedDict, total=False):
    """产物引用集合 —— 所有大产物的定位符都在这里。"""

    bible_ref: str
    series_plan_ref: str
    skeleton_ref: str
    detail_ref: str
    prose_ref: str


class BookState(TypedDict, total=False):
    """书籍级状态（跨阶段共享的轻量字段）。

    各阶段的图都以它为基底扩展。**不含任何大文本。**
    """

    book_id: int
    run_id: int
    #: 全书目标章数
    target_chapters: int
    #: 总卷数（由全卷台阶规划确定，**不硬编码**）
    total_volumes: int
    #: 当前卷号
    current_volume: int
    #: ★ 全书递增章号（唯一权威，D-15）
    current_chapter: int
    #: 产物引用
    refs: GraphRefs
    #: 已完成的工作单元幂等键（供协调器判重）
    done_units: Annotated[list[str], operator.add]


# --------------------------------------------------------------------------- #
# 圣经阶段
# --------------------------------------------------------------------------- #


class BibleState(BookState, total=False):
    """圣经生成阶段。"""

    #: 题材与主题输入
    genre: str
    topic: str
    #: 校验问题（小列表）
    violations: list[str]
    #: 是否通过校验
    ok: bool


# --------------------------------------------------------------------------- #
# 全卷升级台阶阶段（对策②核心）
# --------------------------------------------------------------------------- #


class VolumePlanState(BookState, total=False):
    """全卷升级台阶规划。"""

    #: `SeriesPlan` 的序列化引用（大 JSON 外置）
    series_plan_ref: str
    #: 结构闸门结论（小）
    gate_ok: bool
    gate_issues: list[dict[str, Any]]
    #: 增量补缺轮次（旧项目实测：**禁止全量重写**，大输出必被截断）
    repair_round: int
    #: 待人工确认（C-4 全卷台阶定稿）
    awaiting_confirm: str


# --------------------------------------------------------------------------- #
# 骨架阶段
# --------------------------------------------------------------------------- #


class SkeletonState(BookState, total=False):
    """单卷骨架（分批生成）。"""

    #: 本批覆盖的章号区间
    batch_start: int
    batch_end: int
    batch_no: int
    #: 骨架产物引用
    skeleton_ref: str
    violations: list[str]
    #: ★ 最后一轮未通过校验的骨架**引用**（失败时才有值）。
    #:
    #: 为什么放进 state：`persist_skeleton` 只在成功时写盘，失败后
    #: 读 `skeleton_ref` 得到的是**上一轮的旧骨架** —— 用它做"真缺失 vs
    #: 措辞未命中"的判断会得出自相矛盾的结论（实测踩到）。
    #:
    #: ⚠️ 存 `*_ref` 而不是原文：规则 5 禁止 State 含大文本
    #: （LangGraph checkpoint 膨胀，实测 200 轮 5.3 GB）。
    last_skeleton_ref: str
    #: 待人工确认（C-2 骨架首批）
    awaiting_confirm: str


# --------------------------------------------------------------------------- #
# 细纲阶段
# --------------------------------------------------------------------------- #


class DetailState(BookState, total=False):
    """细纲批次生成。"""

    batch_no: int
    batch_start: int
    batch_end: int
    #: 细纲产物引用（每章一个文件）
    detail_refs: Annotated[list[str], operator.add]
    #: 本批注入的伤势节拍数（供观测）
    health_beats_injected: int
    lint_issues: list[str]
    #: 细纲失败原因（小标量诊断，非产物；成功时为空串）
    #: ★ 缺陷1修复：失败分支的 error 必须进 state，否则调用方无法区分
    #: “真成功”与“首轮 LLM 异常导致的空 lint”（空 lint 即误报成功）
    detail_error: str
    #: 本批内存中产出的计划数（小标量诊断；失败清空时为 0）
    detail_plans: int
    #: ★ 失败批次那**当轮**产物的引用（诊断用；成功时无此键）。
    #:
    #: 失败批次刻意不写 `plans/`（半成品不该被下游读到），
    #: 但"什么都不留"会让诊断无从下手 —— 实测想查报「地点跳变」那章的
    #: `is_transition`，文件根本不存在，只能靠猜。
    #:
    #: ⚠️ 存 **ref** 而非内容：规则 5 禁止 State 含大文本。
    last_plans_ref: str
    awaiting_confirm: str


# --------------------------------------------------------------------------- #
# 单章阶段（P4 核心）
# --------------------------------------------------------------------------- #


class ChapterState(BookState, total=False):
    """单章闭环。

    流程：就绪闸门 → 组装上下文 → 写作 → 打分 → 连续性 →
          路由 →（按需）审校 → 定向重写 → 复检 → 抽取×3 → 提交

    ## 上下文块为什么可以进 state

    写作需要状态表/损伤表/时间线/锚点等**可变尾部**内容。它们都是
    **有界的**（状态表只渲染本章在场实体、时间线只取近几章、前文尾巴
    截断到几百字），且**只在本章的一次调用内使用** ——
    不会像正文/细纲那样随篇幅线性增长。

    ⚠️ 字段名一律用 `*_block` 而不是 `*_text`：
    契约规则 5 禁止 state 字段名以 `text` 结尾（防大文本混入）。
    真正的**大产物**（正文、细纲、抽取结果）只以 `*_ref` 出现。
    """

    global_no: int
    volume_no: int
    #: 细纲引用（写作前必须就绪）
    plan_ref: str
    #: 细纲已就绪到第几章（就绪闸门据此判断）
    detail_ready_through: int
    #: 是否卷末章（无条件送审）
    is_volume_end: bool

    # ── 上下文块（有界；进 prompt 的易变尾部）──
    volume_block: str
    segment_block: str
    states_block: str
    injuries_block: str
    timeline_block: str
    #: 上一章结尾原文（**上游截断到几百字**，不是整章）
    previous_tail: str
    #: 细纲渲染文本（场景拍点表，4~8 拍）
    plan_detail: str
    style_rules: str
    #: 既有实体清单 `[(id, 规范名, 别名)]`，供实体归一
    known_entities: list[tuple[int, str, list[str]]]

    # ── 写作 ──
    prose_ref: str
    prose_hash: str
    prose_chars: int
    write_problems: list[str]

    # ── 打分（小 dict，不是大对象）──
    scorecard: dict[str, Any]
    needs_review: bool
    review_reason: str

    # ── 审校 ──
    review_ref: str
    review_score: float
    review_issues: int
    review_calls: int
    revision_applied: bool
    revision_note: str
    revision_calls: int
    #: 重写计数（超限触发人工确认 C-7）
    rewrite_count: int

    # ── 抽取 ──
    extraction_ref: str
    extraction_calls: int
    extraction_problems: list[str]

    # ── 提交 ──
    committed: bool
    commit_note: str

    #: 失败原因
    error: str
    #: 阶段状态（`ready` / `not_ready` / `blocked` / `written` / `committed` …）
    status: str


__all__ = [
    "GraphRefs",
    "BookState",
    "BibleState",
    "VolumePlanState",
    "SkeletonState",
    "DetailState",
    "ChapterState",
]
