"""节点：风格审校与定向重写（P4-7 / P4-8）。

## 旧项目最贵的单点缺陷：审校说了话，但没人听

`must_review` 全仓 **25 个命中、0 个读取点**（唯一近似读取是写进 trace metadata）。
结果是：

    `style_metrics.needs_rewrite` 的全部"阻断"语义，
    实际强度 = 强制调用 Editor 一次 + 加一段警告；
    Editor 返回 `revised_content: null` 则**正文一字不改入库**。

生产实测：**185/210 章（88.1%）**被点名文风问题，其中 **57 章仍判 passed**。

## 本模块的三条对策

1. **`issues` 非空 ⇒ `revised_content` 必填** —— 由 `ReviewResultOut`
   的 `model_validator` 强制（不是靠 prompt 请求）。旧项目模型曾在
   `notes` 里声称"已改写"而 `revised_content=null`。

2. **不采信模型自述** —— `has_usable_revision` 由**代码**判定：
   有实质修订稿才算修订。`notes` 里的"已改写"不作为判据。

3. **修订必须过闸门才算数** —— 修订稿要重新打分，
   **分数不升则拒绝采用修订**（保留原稿并上报）。
   旧项目实测 `no_improve:6->6` —— 重写空转，成本付了、质量没动。

## 定向重写（P4-8）

审校输出的是**结构化 issues**（含类别与定位），因此重写可以定向：
只把命中的问题类别对应的规则重新强调，而不是重发全章要求"写得更好"。
这是"定向补丁"（P2-9）在写作侧的实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from storyforge.config import get_settings
from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.llm.schemas import ReviewResultOut
from storyforge.infra.prompts.library import build_extractor_layers
from storyforge.scorecard.aggregate import Scorecard, evaluate
from storyforge.scorecard.aggregate import Thresholds

logger = logging.getLogger(__name__)


STYLE_CRITIC_SYSTEM_FILE = "style_critic"

#: 问题类别 → 定向重写时**只强调这一类**的规则。
#:
#: ★ 这是"定向"的具体含义：不是重新要求"写得更好"（那等于重写一遍），
#: 而是把**命中的缺陷**翻译成一条可执行的修改指令。
CATEGORY_RULES: dict[str, str] = {
    "ai_style": (
        "- **AI 味**：删掉套话（仿佛/瞬间/不禁/嘴角勾起/眼中闪过…）；"
        "减少比喻，一章最多留 2~3 处；**不要直接命名情绪**"
        "（不写「他很悲伤」，写他做了什么、身体有什么反应）；"
        "句长要有长短交错，不要每句都差不多长。"
    ),
    "pacing": (
        "- **节奏**：删掉重复复述同一拍的段落；"
        "画面在动但信息没前进的描写要压缩；"
        "对话占比过低时，把部分叙述改成对话呈现。"
    ),
    "consistency": (
        "- **一致性**：严格对照上方人物状态表（伤势/装备/境界/位置）改写冲突处；"
        "角色不得知道状态表里他不知道的事。"
    ),
    "logic": (
        "- **因果**：每个转折补上动机（谁、出于什么目的、用了什么手段）；"
        "删掉无铺垫的巧合。"
    ),
    "foreshadow": (
        "- **伏笔**：本章该埋的伏笔要有具体落点，该回收的要当场兑现。"
    ),
    "ooc": "- **人物**：回到该角色的口癖与立场，不得为剧情需要而失真。",
    "outline_deviation": (
        "- **细纲**：逐条核对细纲场景，把遗漏的关键转折补上。"
        "（若本章是换场章，换地图/换时间是**正常**的，不算偏离。）"
    ),
    "other": "- 按审校意见逐条修改。",
}


@dataclass
class ReviewOutcome:
    """一次审校 + 采用决策的产出。"""

    review: ReviewResultOut | None = None
    #: 最终采用的正文
    final_prose: str = ""
    #: 是否采用了修订稿
    revision_applied: bool = False
    #: 采用/拒绝的理由（必须留痕 —— 旧项目审校结论无人读）
    decision_note: str = ""
    #: 修订前后的分数
    score_before: float = 0.0
    score_after: float = 0.0
    llm_calls: int = 0
    ok: bool = False
    error: str | None = None
    problems: list[str] = field(default_factory=list)


def build_review_user(
    prose: str,
    *,
    plan_detail: str = "",
    states: str = "",
    end_hook: str = "",
    is_transition: bool = False,
) -> str:
    """组装审校的 user 部分。

    ★ 必须把**细纲与状态表**一起给审校 —— 否则它无法判断
    「是否落实细纲」「是否违反人物状态」，只能泛泛评论文笔。
    旧项目 Editor 的 `outline_deviation` 判定就缺少细纲原文对照。
    """
    parts = ["【本章正文】", prose]
    if plan_detail:
        note = (
            "（注意：本章是**换场章**，换地图/换时间是规划内的，不算偏离）"
            if is_transition else ""
        )
        parts += ["", f"【本章细纲·用于核对落实】{note}", plan_detail]
    if states:
        parts += ["", "【人物状态表·用于核对一致性】", states]
    if end_hook:
        parts += ["", f"【规划的章末钩子】{end_hook}"]
    parts += ["", "请审校本章并按要求输出 JSON。"]
    return "\n".join(parts)


async def review_chapter(
    client: LLMClient,
    *,
    book_id: int,
    global_no: int,
    prose: str,
    plan_detail: str = "",
    states: str = "",
    end_hook: str = "",
    is_transition: bool = False,
    idempotency_key: str | None = None,
) -> tuple[ReviewResultOut | None, int]:
    """调用 LLM 审校。返回 `(结果, 实际调用次数)`。"""
    from storyforge.infra.prompts.library import _load  # noqa: PLC0415

    system = _load(STYLE_CRITIC_SYSTEM_FILE)
    layers = build_extractor_layers(
        instructions=system,
        prose=build_review_user(
            prose, plan_detail=plan_detail, states=states,
            end_hook=end_hook, is_transition=is_transition,
        ),
    )
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="style_critic",
        system=messages["system"],
        user=messages["user"],
        schema=ReviewResultOut,
        context=CallContext(
            node="style_critic", book_id=book_id, global_no=global_no,
            idempotency_key=idempotency_key,
        ),
        prompt_prefix=layers.cacheable_prefix(),
    )
    return out, (0 if call.from_cache else 1)


def build_targeted_revision_prompt(
    prose: str, review: ReviewResultOut
) -> str:
    """把结构化 issues 翻译成**定向**修改指令（P4-8）。

    ★ 与"请重写一遍"的区别：这里只列出**命中的缺陷类别**对应的规则，
    并要求保持其余部分不变。旧项目 `no_improve:6->6` 说明
    "泛泛要求写得更好"会空转。
    """
    categories: list[str] = []
    for issue in review.issues:
        if issue.category not in categories:
            categories.append(issue.category)

    rules = [CATEGORY_RULES.get(c, CATEGORY_RULES["other"]) for c in categories]
    detail = "\n".join(
        f"- [{i.severity}/{i.category}] {i.location}：{i.description}"
        + (f"（建议：{i.suggestion}）" if i.suggestion else "")
        for i in review.issues
    )
    return (
        "【定向修改任务】下面是一章已经写好的正文，以及审校发现的问题。\n"
        "请**只针对下列问题**修改，其余内容保持原样（不要重写整个故事线，"
        "不要改变情节走向，不要增删场景）。\n\n"
        f"【发现的问题】\n{detail}\n\n"
        f"【本次修改须遵守的规则】\n" + "\n".join(rules) + "\n\n"
        f"【待修改的正文】\n{prose}\n\n"
        "请输出修改后的**完整正文**（保留所有仍合格的内容，只改问题处）。"
        "只输出正文本身，不要任何说明。"
    )


async def revise_chapter(
    client: LLMClient,
    *,
    book_id: int,
    global_no: int,
    prose: str,
    review: ReviewResultOut,
    idempotency_key: str | None = None,
) -> tuple[str, int]:
    """定向重写。返回 `(新正文, 实际调用次数)`。"""
    from storyforge.infra.prompts.library import _load  # noqa: PLC0415

    system = _load("writer_system")
    user = build_targeted_revision_prompt(prose, review)
    result = await client.complete(
        node="targeted_reviser",
        system=system,
        user=user,
        context=CallContext(
            node="targeted_reviser", book_id=book_id, global_no=global_no,
            idempotency_key=idempotency_key,
        ),
    )
    return (result.text or "").strip(), (0 if result.from_cache else 1)


def _thresholds() -> Thresholds:
    """从配置构造打分卡阈值（**单一来源**）。"""
    s = get_settings().scorecard
    return Thresholds(
        hard_cross_overlap=s.hard_cross_overlap,
        hard_intra_redundancy=s.hard_intra_redundancy,
        hard_dialogue_floor=s.hard_dialogue_floor,
        hard_cliche_hits=s.hard_cliche_hits,
        hard_simile_count=s.hard_simile_count,
        hard_strong_emotion=s.hard_strong_emotion,
        hard_sentence_cv_floor=s.hard_sentence_cv_floor,
        hard_min_chars=s.hard_min_chars,
        hard_outline_adherence=s.hard_outline_adherence,
        target_dialogue_ratio=s.target_dialogue_ratio,
        target_cross_overlap=s.target_cross_overlap,
        target_intra_redundancy=s.target_intra_redundancy,
        target_cliche_hits=s.target_cliche_hits,
        target_simile_count=s.target_simile_count,
        target_strong_emotion=s.target_strong_emotion,
        target_sentence_cv=s.target_sentence_cv,
        target_min_chars=s.target_min_chars,
        target_outline_adherence=s.target_outline_adherence,
        score_threshold=s.score_threshold,
        always_review_chapters=tuple(s.always_review_chapters),
    )


def apply_revision_if_better(
    prose: str,
    revised: str,
    *,
    global_no: int,
    plan=None,
    prev_texts: list[str] | None = None,
    prev_exit=None,
    known_names: set[str] | None = None,
    beat_type: str | None = None,
    is_volume_end: bool = False,
) -> ReviewOutcome:
    """**修订必须过闸门才算数**（P4-7 的核心判据）。

    旧项目实测 `no_improve:6->6` —— 重写空转：成本付了、质量没动、
    而且**没有任何机制发现这件事**。

    本函数：修订稿重新打分，**分数不升则拒绝采用**（保留原稿并留痕）。
    这是把"审校说了话没人听"变成"审校的结论必须被验证"。
    """
    th = _thresholds()
    if not (revised or "").strip():
        return ReviewOutcome(
            final_prose=prose, revision_applied=False,
            decision_note="修订稿为空 —— 拒绝采用（旧项目 no_revised_content 的形态）",
            problems=["修订稿为空"],
        )

    before: Scorecard = evaluate(
        prose, global_no=global_no, plan=plan, prev_texts=prev_texts,
        prev_exit=prev_exit, known_names=known_names, beat_type=beat_type,
        is_volume_end=is_volume_end, thresholds=th,
    )
    after: Scorecard = evaluate(
        revised, global_no=global_no, plan=plan, prev_texts=prev_texts,
        prev_exit=prev_exit, known_names=known_names, beat_type=beat_type,
        is_volume_end=is_volume_end, thresholds=th,
    )

    if after.score > before.score:
        return ReviewOutcome(
            final_prose=revised, revision_applied=True,
            decision_note=(
                f"采用修订稿：{before.score:.3f} → {after.score:.3f}"
                f"（触发项 {len(before.triggers)} → {len(after.triggers)}）"
            ),
            score_before=before.score, score_after=after.score, ok=True,
        )
    return ReviewOutcome(
        final_prose=prose, revision_applied=False,
        decision_note=(
            f"**拒绝修订稿**：{before.score:.3f} → {after.score:.3f}"
            f"（未提升）—— 保留原稿。旧项目实测 no_improve:6->6 即此事，"
            f"但当时无任何机制发现"
        ),
        score_before=before.score, score_after=after.score, ok=True,
        problems=[f"修订未提升分数（{before.score:.3f} → {after.score:.3f}）"],
    )


__all__ = [
    "STYLE_CRITIC_SYSTEM_FILE",
    "CATEGORY_RULES",
    "ReviewOutcome",
    "build_review_user",
    "review_chapter",
    "build_targeted_revision_prompt",
    "revise_chapter",
    "apply_revision_if_better",
]
