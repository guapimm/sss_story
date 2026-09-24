"""打分卡维度规格 —— 把「维度集合」从函数体里抽出来（**表驱动**）。

## 为什么必须抽（审计 §4.4 第 8 条）

`aggregate.py` 此前是 **story-only**：维度名、权重、触发条件
**全部硬编码在 `build_dimensions()` 与 `collect_triggers()` 的函数体里**。
`ScorecardSettings` 只含**阈值**，**没有权重** —— 这是最大缺口。

后果：learn 想用自己的维度集合，只能**复制整个 `aggregate.py`**。
所以本模块是 learn 打分卡的**前提**。

## 设计（三条约束）

1. **story 行为零改变** —— `STORY_DIMENSIONS` 逐条对应原硬编码的
   顺序、权重、判定；`scripts/capture_scorecard_parity.py` 抓了重构前
   快照，重构后 diff 必须为空。
2. **零 LLM**（契约规则 2）—— 本模块只有纯数据与纯函数。
3. **阈值仍由 `Thresholds` 集中提供**，spec 只声明"用哪个指标、
   比哪个阈值字段、什么方向"。

## 为什么用「指标访问器表」而不是字段名字符串

各维度的取值方式不同 —— 有的是裸浮点（`dialogue_ratio`）、
有的要取长度（`len(cliches)`）、有的是集合判断
（`ending not in (OMEN, TIME_SKIP)`）、有的是取反（`not has_state_violation`）。

用字段名 + 一堆特例分支会**把复杂度从"硬编码维度"搬到"硬编码转换规则"**，
没解决问题。故改为：**访问器表**（`ACCESSORS`，14 个具名纯函数）
+ spec 只引用访问器名。spec 仍是**纯数据**（可审查、可 diff），
转换逻辑集中在一处、可单测。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from storyforge.domain.enums import EndingPattern
from storyforge.scorecard.literal import LiteralMetrics
from storyforge.scorecard.structural import StructuralMetrics

#: 比较方向。
#:
#:   `ge`   —— 越大越好（`value >= target` 即通过）
#:   `le`   —— 越小越好（`value <= target` 即通过）
#:   `zero` —— 必须为 0（原实现的 `len(x) == 0` 这类**无阈值**判定）
#:   `flag` —— 值本身即结论（`invert=True` 取反）
#:
#: ★ `zero` 是必要的：硬塞成 `le` 会**凭空造一个阈值字段**，
#: 而那个阈值在 `Thresholds` 里根本不存在（会抛错）。原实现用的是
#: 字面 `== 0`，故如实建模。
Direction = Literal["ge", "le", "zero", "flag"]


@dataclass(frozen=True)
class DimensionSpec:
    """一个质量维度的声明（**数据，不是代码**）。

    - `metric`  —— `ACCESSORS` 里的键（取值方式）
    - `target`  —— `Thresholds` 的目标层字段名；`None` = 不比较阈值
    - `invert`  —— 取反后再判（`flag` 方向下用于"无 X 则通过"）
    - `requires_structural` —— 仅当结构层可用时才产出该维度
    """

    name: str
    metric: str
    direction: Direction
    weight: float = 1.0
    target: str | None = None
    invert: bool = False
    requires_structural: bool = False
    note: str = ""


# --------------------------------------------------------------------------- #
# 指标访问器 —— 每个维度的**取值方式**（集中一处，可单测）
# --------------------------------------------------------------------------- #
#: 签名统一为 `(lit, st) -> float | bool | None`。
#: `None` 一律表示**无法评估**（在 `DimensionScore.earned` 里记 0 分 ——
#: 这是原实现的口径「缺省即不通过」，保持不变）。
ACCESSORS: dict[str, Callable[[LiteralMetrics, StructuralMetrics | None], Any]] = {
    # ── 字面层 ──
    "dialogue_ratio": lambda lit, st: lit.dialogue_ratio,
    "cross_chapter_overlap": lambda lit, st: lit.cross_chapter_overlap,
    "intra_redundancy": lambda lit, st: lit.intra_redundancy,
    # ★ 原实现是 `float(len(lit.cliches))` —— 取条数而非列表本身
    "cliche_count": lambda lit, st: float(len(lit.cliches)),
    "simile_count": lambda lit, st: float(lit.simile_count),
    "strong_emotion_words": lambda lit, st: float(lit.strong_emotion_words),
    "sentence_len_cv": lambda lit, st: lit.sentence_len_cv,
    "chars": lambda lit, st: float(lit.chars),
    # ★ 章首不用时间硬切（原实现 `not lit.starts_with_time_skip`）
    "no_time_skip_start": lambda lit, st: lit.starts_with_time_skip,
    # ★ 章末类型：omen/time_skip 不合格（原实现是一个"不在集合里"判断）
    "ending_not_omen": lambda lit, st: lit.ending not in (
        EndingPattern.OMEN, EndingPattern.TIME_SKIP
    ),
    # ── 结构层（st 可为 None → 返回 None）──
    "outline_adherence": lambda lit, st: None if st is None else st.outline_adherence,
    "hook_landed": lambda lit, st: None if st is None else st.hook_landed,
    # ★ 原实现 `not st.has_state_violation` —— 语义是"无矛盾则通过"
    "no_state_violation": lambda lit, st: (
        None if st is None else (not st.has_state_violation)
    ),
    # ★ 原实现报的是**条数**（`float(len(...))`），不是布尔。
    #   初版我写成返回布尔 → `value` 从 0.0 变成 None（**静默丢掉指标值**，
    #   而 `passed` 恰好不变，总分看不出差别）。被
    #   `scripts/capture_scorecard_parity.py` 的快照 diff 抓到。
    "unsupported_entity_count": lambda lit, st: (
        None if st is None else float(len(st.unsupported_entities))
    ),
}

#: 语义上"已经是最终结论"的访问器 —— 用 `flag` 方向时**不再取反**
#: （`invert` 与之配合表达"无 X 则通过"）
_POSITIVE_FLAGS = frozenset({
    "no_time_skip_start", "ending_not_omen",
    "no_state_violation", "no_unsupported_entities",
})


def _limit_for(spec: DimensionSpec, thresholds: Any) -> Any:
    if spec.target is None:
        return None
    if not hasattr(thresholds, spec.target):
        raise AttributeError(
            f"维度 {spec.name!r} 声明的阈值字段不存在：{spec.target}。"
            f"（拼错会让该维度静默失效 —— 故显式抛错）"
        )
    return getattr(thresholds, spec.target)


def build_dimensions_from_specs(
    specs: tuple[DimensionSpec, ...],
    lit: LiteralMetrics,
    st: StructuralMetrics | None,
    thresholds: Any,
) -> list:
    """按 spec 表构造维度分数（替代原硬编码的 `build_dimensions`）。

    ## 判定口径（与原实现逐条对齐）

    · `ge`   —— `value >= limit`
    · `le`   —— `value <= limit`
    · `flag` —— 值本身即结论；`invert=True` 时取反
    · 任何 `None` → `passed=None`（**无法评估**，记 0 分）
    """
    from storyforge.scorecard.aggregate import DimensionScore

    out: list[DimensionScore] = []
    for spec in specs:
        if spec.requires_structural and st is None:
            continue

        accessor = ACCESSORS.get(spec.metric)
        if accessor is None:
            raise KeyError(
                f"维度 {spec.name!r} 引用了未登记的指标访问器 {spec.metric!r}。"
                f"（合法值：{sorted(ACCESSORS)}）"
            )
        raw = accessor(lit, st)

        if spec.direction == "flag":
            passed: bool | None
            if raw is None:
                passed = None
            else:
                base = bool(raw)
                passed = (not base) if spec.invert else base
            value: float | None = None
        else:
            value = None if raw is None else float(raw)
            if value is None:
                passed = None
            elif spec.direction == "zero":
                # 原实现的 `len(x) == 0` 这类无阈值判定
                passed = value == 0.0
            else:
                limit = float(_limit_for(spec, thresholds))
                passed = value >= limit if spec.direction == "ge" else value <= limit

        detail = spec.note
        if spec.target is not None:
            arrow = "≥" if spec.direction == "ge" else "≤"
            detail = f"目标 {arrow}{_limit_for(spec, thresholds)}"
            if spec.note:
                detail += f"；{spec.note}"

        out.append(DimensionScore(
            name=spec.name, value=value, passed=passed,
            weight=spec.weight, detail=detail,
        ))
    return out


# --------------------------------------------------------------------------- #
# ★ story 维度表
# --------------------------------------------------------------------------- #
#: 逐条对应原 `build_dimensions()` 的顺序、权重、判定。
#:
#: ⚠️ **改这张表就是改 story 的打分行为。** 改动前后必须跑
#: `scripts/capture_scorecard_parity.py` 并 diff 快照（golden delta 必须为 0）。
STORY_DIMENSIONS: tuple[DimensionSpec, ...] = (
    DimensionSpec(
        name="dialogue_ratio", metric="dialogue_ratio", direction="ge",
        target="target_dialogue_ratio", weight=1.5,
        note="（旧基线 p50=0.106）",
    ),
    DimensionSpec(
        name="cross_chapter_overlap", metric="cross_chapter_overlap",
        direction="le", target="target_cross_overlap", weight=1.5,
        note="（旧基线 p50=0.099）",
    ),
    DimensionSpec(
        name="intra_redundancy", metric="intra_redundancy", direction="le",
        target="target_intra_redundancy", weight=1.0,
        note="（旧基线 p50=0.062）",
    ),
    DimensionSpec(
        name="cliche_hits", metric="cliche_count", direction="le",
        target="target_cliche_hits", weight=1.0,
    ),
    DimensionSpec(
        name="simile_count", metric="simile_count", direction="le",
        target="target_simile_count", weight=1.0,
        note="（用户痛点：比喻太多）",
    ),
    DimensionSpec(
        name="strong_emotion_words", metric="strong_emotion_words",
        direction="le", target="target_strong_emotion", weight=0.8,
        note="（旧 writer 规则）",
    ),
    DimensionSpec(
        name="sentence_len_cv", metric="sentence_len_cv", direction="ge",
        target="target_sentence_cv", weight=0.7,
        note="（旧基线 p50=0.651）",
    ),
    DimensionSpec(
        name="chars", metric="chars", direction="ge",
        target="target_min_chars", weight=1.2,
    ),
    DimensionSpec(
        name="not_time_skip_start", metric="no_time_skip_start",
        direction="flag", weight=0.5, invert=True,
        note="章首不用时间硬切",
    ),
    DimensionSpec(
        name="ending_pattern", metric="ending_not_omen", direction="flag",
        weight=0.8, note="章末 omen/time_skip 不合格",
    ),
    # ── 结构层 ──
    DimensionSpec(
        name="outline_adherence", metric="outline_adherence", direction="ge",
        target="target_outline_adherence", weight=2.0,
        requires_structural=True,
    ),
    DimensionSpec(
        name="hook_landed", metric="hook_landed", direction="flag",
        weight=1.2, requires_structural=True, note="章末落在规划钩子",
    ),
    DimensionSpec(
        name="state_consistency", metric="no_state_violation",
        direction="flag", weight=1.5, requires_structural=True,
        note="无状态矛盾",
    ),
    DimensionSpec(
        name="unsupported_entities", metric="unsupported_entity_count",
        direction="zero", weight=0.6, requires_structural=True,
        note="无无依据新名词",
    ),
)


__all__ = [
    "ACCESSORS",
    "DimensionSpec",
    "Direction",
    "STORY_DIMENSIONS",
    "build_dimensions_from_specs",
]
