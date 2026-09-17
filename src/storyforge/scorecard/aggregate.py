"""打分卡汇总与触发判定（决策 D-10）。

## 设计依据

用户原话：「建议每章，但是按节拍、伏笔、风险不太可控，**如果可以量化质量就选 A**」。

因此本模块的定位是：**确定性打分卡负责「发现」，LLM 负责「判断与改写」**。

    ┌──────────────────────────────────────────────────────┐
    │ 每章：确定性打分卡（零 LLM、可复现、毫秒级）            │
    │   → 达标：直接提交                                     │
    │   → 不达标 / 命中触发规则：送 LLM 审校 + 定向重写        │
    │   → 无条件送审：黄金三章 / 卷末 / 高潮章（D-14 C-5）     │
    └──────────────────────────────────────────────────────┘

## 旧项目的对照

旧项目实测 `review_config.json`：
```json
{ "global": { "sample_rate": 1.0,
    "head_tail_by_scale": { "fast":[1,2], "standard":[5,7], "grand":[10,10], "ultra":[20,15] } } }
```
→ **standard 档只审头 5 章 + 尾 7 章**。100 章的书 **88 章完全不审**。
ultra 档头 20 尾 15 → 400 章的书 **365 章不审**。

而实测结果（本次独立复核 1,441 章 `review_json`）：
  · 被点名 `ai_style`/`pacing` 的章 **185/210 = 88.1%**
  · 其中**仍判 passed**（score ≥ 7）的 **57 章 = 30.8%**
  · `ai_style` 以 **228 条**居全库 issue 首位

→ **覆盖率与判罚力度双重不足**。本模块的确定性打分卡让**每一章都有分**，
不依赖采样。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from storyforge.domain.enums import EndingPattern, TriggerReason
from storyforge.domain.outline import ChapterPlan, WorldStateSnapshot
from storyforge.scorecard.literal import LiteralMetrics
from storyforge.scorecard.literal import analyze as analyze_literal
from storyforge.scorecard.structural import StructuralMetrics, analyze_structural

#: `TriggerReason` 定义在 `domain/enums.py`（枚举单一来源，由 tests/contract 规则 8 强制）。
#: 此处重新导出以便调用方从本模块导入。
__all__ = [
    "TriggerReason",
    "Thresholds",
    "DimensionScore",
    "Scorecard",
    "build_dimensions",
    "collect_triggers",
    "evaluate",
]


@dataclass
class Thresholds:
    """打分卡阈值 —— **双层设计**（hard / target），由真实分布标定。

    ## 为什么必须双层

    初版用单层阈值（如 `dialogue_ratio_min = 0.25`），实测结果：**47/47 章全部触发**
    （书 23 的 dialogue p95 才 0.292）→ 闸门退化成"全部送审"，等于没有闸门。

    单层阈值的两难：
      · 定得严格 → 每章都触发，LLM 审校成本爆炸，闸门失去筛选意义
      · 定得宽松 → 只有明显坏章被抓，改善动力不足

    因此分两层：

      `hard_*`   —— 明确缺陷。**任一命中即触发审校**（少而准）
      `target_*` —— 目标水位。参与**总分**计算，其自身不单独触发

    这样：clearly-bad 的章被 hard 层抓住；整体水位由 score 驱动，逐期抬升。

    ## 标定依据（旧项目书 23 的 47 章真实分布）

    | 指标 | p5 | p25 | p50 | p75 | p95 |
    |---|---|---|---|---|---|
    | `dialogue_ratio` | 0.028 | 0.073 | **0.106** | 0.132 | 0.292 |
    | `cross_chapter_overlap` | 0.059 | 0.084 | **0.099** | 0.105 | 0.119 |
    | `intra_redundancy` | 0.039 | 0.051 | **0.062** | 0.071 | 0.098 |
    | `sentence_len_cv` | 0.558 | 0.612 | **0.651** | 0.671 | 0.701 |
    | `chars` | 4149 | 4609 | **5065** | 5433 | 6520 |

    ⚠️ **这张表本身是"要超越的基线"，不是"合格线"**。
    书 23 正是用户抱怨的那一本（「AI 味重、过于啰嗦」），
    所以 `target_*` 定在**优于旧 p50** 的位置，`hard_*` 定在旧 p95/max 附近。

    标定值来源：`tests/golden/test_baseline_reproduction.py` 可复现这些分位数。
    """

    # ── 字面层：hard（明确缺陷，任一命中即触发）──
    #: 跨章重叠硬上限（旧 max = 0.145；留余量）
    hard_cross_overlap: float = 0.15
    #: 章内冗余硬上限（旧 max = 0.122）
    hard_intra_redundancy: float = 0.15
    #: 对话占比硬下限（旧 p5 = 0.028，取略高）
    hard_dialogue_floor: float = 0.05
    #: 套话硬上限（旧 p95 = 2，取宽松值；套话本身不是硬伤）
    hard_cliche_hits: int = 8
    #: 比喻硬上限（旧 p95 = 1；用户痛点「比喻太多」）
    hard_simile_count: int = 6
    #: 强情绪词硬上限（旧 writer 规则：每章 ≤2 次）
    hard_strong_emotion: int = 4
    #: 句长变异硬下限（旧 p5 = 0.558，极低说明句子过于均匀）
    hard_sentence_cv_floor: float = 0.30
    #: 篇幅硬下限
    hard_min_chars: int = 2500

    # ── 字面层：target（参与总分，逐期抬升）──
    target_dialogue_ratio: float = 0.25
    target_cross_overlap: float = 0.06
    target_intra_redundancy: float = 0.08
    target_cliche_hits: int = 2
    target_simile_count: int = 3
    target_strong_emotion: int = 2
    target_sentence_cv: float = 0.50
    target_min_chars: int = 3500

    # ── 结构层 ──
    #: 细纲落实率硬下限（低于此值说明细纲基本没落实）
    hard_outline_adherence: float = 0.5
    #: 细纲落实率目标
    target_outline_adherence: float = 0.8

    # ── 总分 ──
    #: 总分低于此值 → 触发 `LOW_SCORE`。
    #: 标定说明：47 章实测 score p50 = 0.700（无细纲，仅字面层）。
    #: 0.62 对应"比旧基线中位数明显更差"的章节。
    score_threshold: float = 0.62

    #: 无条件送审的章号（黄金三章，D-14 C-5）
    always_review_chapters: tuple[int, ...] = (1, 2, 3)


@dataclass
class DimensionScore:
    """单个维度的得分与说明。"""

    name: str
    value: float | None
    passed: bool | None      # None = 无法评估（缺省即不通过）
    weight: float = 1.0
    detail: str = ""

    @property
    def earned(self) -> float:
        """得分（无法评估记 0 —— 「缺省即不通过」）。"""
        if self.passed is None:
            return 0.0
        return self.weight if self.passed else 0.0


@dataclass
class Scorecard:
    """一章的确定性打分卡。"""

    global_no: int
    literal: LiteralMetrics | None = None
    structural: StructuralMetrics | None = None
    dimensions: list[DimensionScore] = field(default_factory=list)
    triggers: list[TriggerReason] = field(default_factory=list)
    #: 加权总分（0~1）
    score: float = 0.0
    #: 是否需要 LLM 审校
    needs_review: bool = False
    #: 是否无条件审（黄金三章/卷末/高潮）
    forced_review: bool = False

    @property
    def passed(self) -> bool:
        return self.score >= 0.0 and not self.triggers and not self.needs_review

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_no": self.global_no,
            "score": self.score,
            "passed": self.passed,
            "needs_review": self.needs_review,
            "forced_review": self.forced_review,
            "triggers": [t.value for t in self.triggers],
            "dimensions": [
                {
                    "name": d.name,
                    "value": d.value,
                    "passed": d.passed,
                    "weight": d.weight,
                    "detail": d.detail,
                }
                for d in self.dimensions
            ],
            "literal": self.literal.to_dict() if self.literal else None,
            "structural": self.structural.to_dict() if self.structural else None,
        }

    def failed_dimensions(self) -> list[DimensionScore]:
        return [d for d in self.dimensions if d.passed is False]

    def summary(self) -> str:
        if self.passed:
            return f"第{self.global_no}章 打分卡通过（{self.score:.2f}）"
        reasons = ", ".join(t.value for t in self.triggers) or "低分"
        return f"第{self.global_no}章 需审校（{self.score:.2f}）：{reasons}"


# --------------------------------------------------------------------------- #
# 维度构造
# --------------------------------------------------------------------------- #


def _dim(
    name: str,
    value: float | None,
    passed: bool | None,
    *,
    weight: float = 1.0,
    detail: str = "",
) -> DimensionScore:
    return DimensionScore(name=name, value=value, passed=passed, weight=weight, detail=detail)


def _le(value: float | None, limit: float) -> bool | None:
    """`value <= limit`，`None` → `None`（缺省即不通过）。"""
    return None if value is None else value <= limit


def _ge(value: float | None, limit: float) -> bool | None:
    return None if value is None else value >= limit


def build_dimensions(
    lit: LiteralMetrics, st: StructuralMetrics | None, th: Thresholds
) -> list[DimensionScore]:
    """把字面层与结构层指标翻译成维度分数。

    ⚠️ 维度用 **target 层**判定（参与总分），**不参与单独触发** ——
    触发由 `collect_triggers` 用 hard 层单独判。这是双层设计的关键：
    若维度判定直接触发审校，47/47 章都会触发（实测），闸门失去筛选意义。

    `detail` 里同时给出 hard 与 target 两个水位，便于人工判读。
    """
    dims: list[DimensionScore] = [
        _dim(
            "dialogue_ratio",
            lit.dialogue_ratio,
            _ge(lit.dialogue_ratio, th.target_dialogue_ratio),
            weight=1.5,
            detail=(
                f"目标 ≥{th.target_dialogue_ratio}，硬底线 ≥{th.hard_dialogue_floor}"
                f"（旧基线 p50=0.106）"
            ),
        ),
        _dim(
            "cross_chapter_overlap",
            lit.cross_chapter_overlap,
            _le(lit.cross_chapter_overlap, th.target_cross_overlap),
            weight=1.5,
            detail=(
                f"目标 ≤{th.target_cross_overlap}，硬上限 ≤{th.hard_cross_overlap}"
                f"（旧基线 p50=0.099）"
            ),
        ),
        _dim(
            "intra_redundancy",
            lit.intra_redundancy,
            _le(lit.intra_redundancy, th.target_intra_redundancy),
            weight=1.0,
            detail=f"目标 ≤{th.target_intra_redundancy}（旧基线 p50=0.062）",
        ),
        _dim(
            "cliche_hits",
            float(len(lit.cliches)),
            _le(float(len(lit.cliches)), float(th.target_cliche_hits)),
            weight=1.0,
            detail=f"目标 ≤{th.target_cliche_hits}",
        ),
        _dim(
            "simile_count",
            float(lit.simile_count),
            _le(float(lit.simile_count), float(th.target_simile_count)),
            weight=1.0,
            detail=f"目标 ≤{th.target_simile_count}（用户痛点：比喻太多）",
        ),
        _dim(
            "strong_emotion_words",
            float(lit.strong_emotion_words),
            _le(float(lit.strong_emotion_words), float(th.target_strong_emotion)),
            weight=0.8,
            detail=f"目标 ≤{th.target_strong_emotion}（旧 writer 规则）",
        ),
        _dim(
            "sentence_len_cv",
            lit.sentence_len_cv,
            _ge(lit.sentence_len_cv, th.target_sentence_cv),
            weight=0.7,
            detail=f"目标 ≥{th.target_sentence_cv}（旧基线 p50=0.651）",
        ),
        _dim(
            "chars",
            float(lit.chars),
            _ge(float(lit.chars), float(th.target_min_chars)),
            weight=1.2,
            detail=f"目标 ≥{th.target_min_chars}，硬底线 ≥{th.hard_min_chars}",
        ),
        _dim(
            "not_time_skip_start",
            None,
            not lit.starts_with_time_skip,
            weight=0.5,
            detail="章首不用时间硬切",
        ),
    ]

    # 章末类型：omen（贴片式假钩子）与 time_skip 是问题。
    # ★ summary **只观测不判罚** —— 旧项目实测 summary=0/time_skip=0，
    #   说明「不要总结式结尾」的规则本就生效，不是硬伤。
    ending_ok = lit.ending not in (EndingPattern.OMEN, EndingPattern.TIME_SKIP)
    dims.append(
        _dim(
            "ending_pattern",
            None,
            ending_ok,
            weight=0.8,
            detail=f"章末={lit.ending.value}（omen/time_skip 不合格）",
        )
    )

    if st is not None:
        dims.extend(
            [
                _dim(
                    "outline_adherence",
                    st.outline_adherence,
                    _ge(st.outline_adherence, th.target_outline_adherence),
                    weight=2.0,
                    detail=(
                        f"目标 ≥{th.target_outline_adherence}，"
                        f"硬底线 ≥{th.hard_outline_adherence}"
                    ),
                ),
                _dim(
                    "hook_landed",
                    None,
                    st.hook_landed,
                    weight=1.2,
                    detail="章末落在规划钩子",
                ),
                _dim(
                    "state_consistency",
                    None,
                    not st.has_state_violation,
                    weight=1.5,
                    detail="无状态矛盾",
                ),
                _dim(
                    "unsupported_entities",
                    float(len(st.unsupported_entities)),
                    len(st.unsupported_entities) == 0,
                    weight=0.6,
                    detail="无无依据新名词",
                ),
            ]
        )
    return dims


# --------------------------------------------------------------------------- #
# 触发判定
# --------------------------------------------------------------------------- #


def collect_triggers(
    lit: LiteralMetrics,
    st: StructuralMetrics | None,
    th: Thresholds,
    *,
    global_no: int,
    is_volume_end: bool = False,
    beat_type: str | None = None,
) -> tuple[list[TriggerReason], bool]:
    """收集触发理由。返回 `(triggers, forced_review)`。

    ★ 只用 **hard 层**判定触发 —— hard 层是"明确缺陷"，少而准。
    target 层影响总分（`LOW_SCORE`），不单独触发。
    这是双层设计的目的：**闸门必须能筛掉大部分章节**，否则等于没有闸门。

    `forced_review` = 无条件送 LLM 审（黄金三章 / 卷末 / 高潮，D-14）。
    """
    triggers: list[TriggerReason] = []
    forced = False

    # ── 无条件审（D-14 C-5 黄金三章 + 结构关键章）──
    if global_no in th.always_review_chapters:
        forced = True
        triggers.append(TriggerReason.GOLDEN_THREE)
    if is_volume_end:
        forced = True
        triggers.append(TriggerReason.VOLUME_END)
    if beat_type in ("climax", "boss"):
        forced = True
        triggers.append(TriggerReason.CLIMAX_BOSS)

    # ── hard 层触发（明确缺陷）──
    if lit.cross_chapter_overlap is not None and lit.cross_chapter_overlap > th.hard_cross_overlap:
        triggers.append(TriggerReason.CROSS_OVERLAP)
    if lit.intra_redundancy is not None and lit.intra_redundancy > th.hard_intra_redundancy:
        triggers.append(TriggerReason.INTRA_REDUNDANCY)
    if lit.dialogue_ratio is not None and lit.dialogue_ratio < th.hard_dialogue_floor:
        triggers.append(TriggerReason.DIALOGUE_LOW)
    if len(lit.cliches) > th.hard_cliche_hits:
        triggers.append(TriggerReason.CLICHE_HEAVY)
    if lit.simile_count > th.hard_simile_count:
        triggers.append(TriggerReason.SIMILE_HEAVY)
    if lit.strong_emotion_words > th.hard_strong_emotion:
        triggers.append(TriggerReason.EMOTION_NAMING)
    if lit.sentence_len_cv is not None and lit.sentence_len_cv < th.hard_sentence_cv_floor:
        triggers.append(TriggerReason.SENTENCE_UNIFORM)
    if lit.chars < th.hard_min_chars:
        triggers.append(TriggerReason.TOO_SHORT)
    if lit.starts_with_time_skip:
        triggers.append(TriggerReason.BRIDGE_BREAK)
    if lit.ending in (EndingPattern.OMEN, EndingPattern.TIME_SKIP):
        triggers.append(TriggerReason.ENDING_OMEN)
    # ★ AI 画面收尾（角色微笑/望远方）—— 独立信号，**不是** `ending` 的子类。
    #   旧项目接在 `needs_rewrite` 上（`style_metrics.py:131`），只看末 60 字。
    #   实测触发率 0.79%（全语料 254 章命中 2 章），判定准确。
    if lit.ai_scene_ending:
        triggers.append(TriggerReason.AI_SCENE_ENDING)

    if st is not None:
        if st.outline_adherence is not None and st.outline_adherence < th.hard_outline_adherence:
            triggers.append(TriggerReason.OUTLINE_DEVIATION)
        if st.hook_landed is False:
            triggers.append(TriggerReason.HOOK_MISSED)
        if st.has_state_violation:
            triggers.append(TriggerReason.STATE_CONFLICT)
        if st.unsupported_entities:
            triggers.append(TriggerReason.UNSUPPORTED_ENTITY)

    return triggers, forced


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def evaluate(
    prose: str,
    *,
    global_no: int,
    plan: ChapterPlan | None = None,
    prev_texts: list[str] | None = None,
    prev_exit: WorldStateSnapshot | None = None,
    known_names: set[str] | None = None,
    beat_type: str | None = None,
    is_volume_end: bool = False,
    thresholds: Thresholds | None = None,
) -> Scorecard:
    """对一章正文打分（**零 LLM、可复现**）。

    这是 D-10 的核心：让每一章都有确定性分数，不依赖审校采样。
    """
    th = thresholds or Thresholds()
    lit = analyze_literal(prose, prev_texts=prev_texts)
    st = (
        analyze_structural(prose, plan, prev_exit=prev_exit, known_names=known_names)
        if plan is not None
        else None
    )

    dims = build_dimensions(lit, st, th)
    total_weight = sum(d.weight for d in dims) or 1.0
    score = sum(d.earned for d in dims) / total_weight

    triggers, forced = collect_triggers(
        lit, st, th, global_no=global_no,
        is_volume_end=is_volume_end, beat_type=beat_type,
    )
    if score < th.score_threshold and TriggerReason.LOW_SCORE not in triggers:
        triggers.append(TriggerReason.LOW_SCORE)

    needs = forced or bool(triggers)
    return Scorecard(
        global_no=global_no,
        literal=lit,
        structural=st,
        dimensions=dims,
        triggers=triggers,
        score=score,
        needs_review=needs,
        forced_review=forced,
    )


__all__ = [
    "TriggerReason",
    "Thresholds",
    "DimensionScore",
    "Scorecard",
    "build_dimensions",
    "collect_triggers",
    "evaluate",
]
