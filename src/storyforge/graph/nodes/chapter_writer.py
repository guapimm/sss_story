"""节点：单章正文写作（P4-1 / P4-3）。

## 本节点是「恢复率 0%」在**写作侧**的落地

旧项目把两种互相冲突的指令同时发给 writer：

    【损伤区】⚠️已超恢复期，本轮必须推进
    【细纲区】最高优先级、禁止新增细纲之外的重要转折

实测 writer **服从细纲**（这是正确的服从）→ 恢复永不发生。

本节点拿到的细纲**已经把恢复排进场景**（`detail_planner` 注入
`health_beats`），因此 `health_block` 发出的是**有场景支撑的指令**，
writer 不再面对矛盾。这是同一条根因链的最后一环：

    HealthScheduler（确定性）→ 细纲（占真实场景）→ writer（照写）

## 三层 prompt（对策⑦，缓存命中的关键）

    stable   = writer_system.md + 世界观圣经        ← 全书不变，逐字节相同
    semi     = 本卷台阶 + 本段目标 + 文风规则        ← 卷内/段内不变
    volatile = 状态/关系/损伤/时间线/锚点/细纲/节拍/钩子/**字数** ← 每章变

★ 字数参数**必须**在易变层。旧项目用 `.format(MIN_CHARS=...)` 注入 system，
导致 system 逐章变化 → 缓存前缀逐章失效。

## 长度闭环（`[writer]` 配置）

    min_chars   目标下限 → 不足则**续写一次**（不是重写整章，省钱且不丢内容）
    soft_max    硬性上限 → 超过只**记录不截断**（截断会切断句子并丢钩子）

为什么上限不自动重写：旧项目实测"重写空转"（`no_improve:6->6`）——
重写一次的成本等于再写一章，而超长本身是可控缺陷（由打分卡记录并送审）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from storyforge.config import get_settings
from storyforge.domain.enums import BeatType
from storyforge.domain.outline import ChapterPlan, WorldStateSnapshot
from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.prompts.library import ChapterContext, build_chapter_layers
from storyforge.infra.storage.artifacts import ArtifactStore, content_hash

logger = logging.getLogger(__name__)


#: 拒答/元回应检测 —— 模型不写正文而写说明时的特征。
#:
#: 旧项目实测 22.2% 空输出，但**非空却无正文**的输出同样致命且更隐蔽：
#: 它会作为"正文"入库（打分卡算得出字数，读者读到的却是道歉信）。
REFUSAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("中文道歉", re.compile(r"抱歉[，,].{0,12}(无法|不能|不便)")),
    ("英文道歉", re.compile(r"I (?:am|'m) sorry|I (?:cannot|can't|can not)", re.I)),
    ("自称AI", re.compile(r"作为(一个)?(AI|人工智能|语言模型)")),
    ("要求澄清", re.compile(r"(请|能否)(提供|告知)更多(信息|细节|上下文)")),
    ("元说明", re.compile(r"^(以下是|这是)(为|我)(你|您)?(撰写|生成|编写)")),
    ("代码块包裹", re.compile(r"^```")),
)

#: 续写时的接续指令（只发尾部，不重发全书上下文）
CONTINUE_INSTRUCTION = (
    "【续写指令】上面的正文在末尾被截断了。请**直接从断点处继续写下去**，"
    "补足到目标字数。\n"
    "要求：\n"
    "- 不要重复已经写过的内容，不要重新开头，不要加「（续）」之类的标记；\n"
    "- 保持同一场景、同一语气、同一时态，无缝衔接；\n"
    "- 只输出续写的部分本身。"
)


@dataclass
class WriteResult:
    """一次写作的产出。

    ★ 正文**不放在 state 里** —— 只有 ref + hash + 字数（契约规则 5/19）。
    """

    prose: str = ""
    prose_ref: str = ""
    prose_hash: str = ""
    chars: int = 0
    llm_calls: int = 0
    #: 是否做了续写补齐
    continued: bool = False
    #: 问题清单（拒答、过短、过长…）
    problems: list[str] = field(default_factory=list)
    ok: bool = False
    error: str | None = None


# --------------------------------------------------------------------------- #
# 上下文组装（P4-1）
# --------------------------------------------------------------------------- #


def render_plot_detail(plan: ChapterPlan) -> str:
    """把细纲的场景拍点表渲染成**硬约束文本**。

    继承旧项目**已验证有效**的格式（`flowmode/base.py`）：
    「场景/人物：目标→阻碍→结果」，双向硬约束（防遗漏 + 防加戏）。
    新增 `why_now` 一行，让动机由上一拍逼出。
    """
    lines: list[str] = []
    for i, s in enumerate(plan.scenes):
        lines.append(
            f"{i + 1}. 目标：{s.goal}\n"
            f"   阻碍：{s.obstacle}\n"
            f"   结果：{s.outcome}\n"
            f"   为何是现在：{s.why_now}"
        )
    if plan.plant_foreshadowings:
        lines.append("本章需埋下伏笔：" + "、".join(plan.plant_foreshadowings))
    if plan.resolve_foreshadowings:
        lines.append("本章需回收伏笔：" + "、".join(plan.resolve_foreshadowings))
    return "\n".join(lines)


def render_health_beats(plan: ChapterPlan) -> str:
    """把本章的伤势节拍渲染成指令文本（进易变层）。

    ★ 与旧项目的关键差异：这些节拍**已经占用了本章的真实场景**
    （由 `detail_planner.attach_health_beats` 写入 `scene_seq`），
    因此 writer 收到的是可执行指令，而不是与细纲冲突的催促。
    """
    if not plan.health_beats:
        return ""
    lines: list[str] = []
    for b in plan.health_beats:
        lines.append(f"- 第{b.scene_seq + 1}个场景：{b.note}")
    return "\n".join(lines)


def render_state(s: WorldStateSnapshot | None) -> str:
    """把结构化状态渲染成一行文本（锚点用）。"""
    if s is None:
        return ""
    parts: list[str] = []
    if s.location_name or s.location_entity_id is not None:
        parts.append(f"地点：{s.location_name or s.location_entity_id}")
    if s.story_time:
        parts.append(f"时间：{s.story_time}")
    if s.present_names or s.present_entity_ids:
        names = s.present_names or [str(i) for i in s.present_entity_ids]
        parts.append(f"在场：{'、'.join(names)}")
    if s.known_facts:
        known = "；".join(
            f"{k}知道{('、'.join(v))}" for k, v in s.known_facts.items() if v
        )
        if known:
            parts.append(f"知情：{known}")
    if s.open_thread:
        parts.append(f"未收束：{s.open_thread}")
    return "；".join(parts)


def assemble_context(
    plan: ChapterPlan,
    *,
    prev_exit: WorldStateSnapshot | None = None,
    next_plan: ChapterPlan | None = None,
    states: str = "",
    relations: str = "",
    injuries: str = "",
    timeline: str = "",
    rag_snippets: str = "",
    previous_tail: str = "",
    style_rules: str = "",
) -> ChapterContext:
    """组装一章的写作上下文（P4-1）。

    ★ 两个锚点必须都给（继承旧项目已验证有效的机制）：
    旧项目实测章间衔接断裂是最严重缺陷 —— 根因**不是细纲长度**，
    而是 writer 只知道自己这一章。给出上一章出口与下一章入口，
    章末才有必须抵达的位置。
    """
    return ChapterContext(
        global_no=plan.global_no,
        volume_no=plan.volume_no,
        title=plan.title,
        plot=plan.plot,
        plot_detail=render_plot_detail(plan),
        end_hook=plan.end_hook,
        prev_exit_state=render_state(prev_exit),
        next_entry_state=render_state(next_plan.entry_state) if next_plan else "",
        next_title=next_plan.title if next_plan else "",
        states=states,
        relations=relations,
        injuries=injuries,
        foreshadow_plant=list(plan.plant_foreshadowings),
        foreshadow_resolve=list(plan.resolve_foreshadowings),
        timeline=timeline,
        rag_snippets=rag_snippets,
        previous_tail=previous_tail,
        health_beats=render_health_beats(plan),
        beat_type=plan.beat_type.value,
        is_transition=plan.is_transition,
        style_rules=style_rules,
    )


# --------------------------------------------------------------------------- #
# 检测
# --------------------------------------------------------------------------- #


def detect_refusal(text: str) -> str | None:
    """检测"非空但不是正文"的输出。返回命中的特征名（无则 `None`）。

    这类输出比空输出更危险：空输出会被 `CallStatus.BLANK` 抓到，
    而"道歉信"是**非空**的，会作为正文一路入库。
    """
    head = text.lstrip()[:400]
    for label, pattern in REFUSAL_PATTERNS:
        if pattern.search(head):
            return label
    return None


def count_chars(text: str) -> int:
    """正文字数（按字符计，含标点 —— 与打分卡口径一致）。"""
    return len(re.sub(r"\s", "", text or ""))


def _normalized(text: str) -> str:
    return re.sub(r"\s", "", text or "")


def looks_like_restart(existing: str, tail: str, *, probe: int = 30) -> bool:
    """续写内容是否其实是**从头重写**（而不是接着写）。

    ★ 为什么必须检测：模型被要求"继续写"时经常重新开头。
    若直接拼接，正文会把已经写过的内容**再贴一遍** ——
    长度检查看着"达标"了，内容却是重复的。这是最隐蔽的一类坏输出：
    字数对、拒答检测过、打分卡也只看到"篇幅够"。

    判据：续写开头 `probe` 个字符（去空白后）是否已出现在已写正文里。
    取 30 个字是因为正常续写不会与上文有 30 字的逐字重合。
    """
    head = _normalized(tail)[:probe]
    if len(head) < probe:
        return False
    return head in _normalized(existing)


# --------------------------------------------------------------------------- #
# 写作
# --------------------------------------------------------------------------- #


async def write_chapter(
    client: LLMClient,
    *,
    book_id: int,
    plan: ChapterPlan,
    bible_text: str,
    volume_block_text: str,
    segment_block_text: str = "",
    prev_exit: WorldStateSnapshot | None = None,
    next_plan: ChapterPlan | None = None,
    states: str = "",
    relations: str = "",
    injuries: str = "",
    timeline: str = "",
    rag_snippets: str = "",
    previous_tail: str = "",
    style_rules: str = "",
    min_chars: int | None = None,
    soft_max: int | None = None,
    max_continue: int = 1,
    idempotency_key: str | None = None,
) -> WriteResult:
    """写一章正文（含长度闭环与拒答检测）。

    ★ 幂等键按章固定。LangGraph `#7417`：>180s 的调用会被**静默重放** ——
    而正文生成正是最容易超 180s 的调用。
    """
    settings = get_settings()
    lo = min_chars if min_chars is not None else settings.writer.min_chars
    hi = soft_max if soft_max is not None else settings.writer.soft_max

    ctx = assemble_context(
        plan, prev_exit=prev_exit, next_plan=next_plan, states=states,
        relations=relations, injuries=injuries, timeline=timeline,
        rag_snippets=rag_snippets, previous_tail=previous_tail,
        style_rules=style_rules,
    )
    layers = build_chapter_layers(
        bible_text=bible_text,
        volume_block_text=volume_block_text,
        segment_block_text=segment_block_text,
        ctx=ctx,
        min_chars=lo,
        soft_max=hi,
        style_rules=style_rules,
    )
    # ★ `assemble` 会跑 `validate_stability`：稳定层被易变内容污染时**抛错**。
    # 这是把"缓存命中率"从"事后统计"变成"事前保证"的那一步。
    messages = layers.assemble()

    call_ctx = CallContext(
        node="chapter_writer", book_id=book_id, global_no=plan.global_no,
        volume_no=plan.volume_no, idempotency_key=idempotency_key,
    )

    problems: list[str] = []
    calls = 0
    try:
        result = await client.complete(
            node="chapter_writer",
            system=messages["system"],
            user=messages["user"],
            context=call_ctx,
            context_breakdown=None,
            prompt_prefix=layers.cacheable_prefix(),
        )
        if not result.from_cache:
            calls += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("chapter write failed: %s", exc)
        return WriteResult(llm_calls=calls, ok=False, error=f"写作调用失败：{exc}")

    prose = (result.text or "").strip()

    # ── 拒答检测（"非空但不是正文"）──
    refusal = detect_refusal(prose)
    if refusal is not None:
        return WriteResult(
            prose="", chars=0, llm_calls=calls, ok=False, problems=[f"拒答：{refusal}"],
            error=f"模型输出了说明文字而非正文（特征：{refusal}）",
        )

    # ── 长度闭环：不足则**续写**（不重写整章）──
    continued = False
    for _ in range(max_continue):
        if count_chars(prose) >= lo:
            break
        try:
            cont = await client.complete(
                node="chapter_writer",
                system=messages["system"],
                user=(
                    f"{messages['user']}\n\n【已写正文】\n{prose}\n\n"
                    f"{CONTINUE_INSTRUCTION}"
                ),
                context=CallContext(
                    node="chapter_writer", book_id=book_id,
                    global_no=plan.global_no, volume_no=plan.volume_no,
                    idempotency_key=(
                        f"{idempotency_key}:cont" if idempotency_key else None
                    ),
                ),
                prompt_prefix=layers.cacheable_prefix(),
            )
            if not cont.from_cache:
                calls += 1
            tail = (cont.text or "").strip()
            if not tail or detect_refusal(tail) is not None:
                problems.append("续写失败：返回空或说明文字")
                break
            if looks_like_restart(prose, tail):
                # ★ 模型从头重写了 —— 拼接会得到"正文重复一遍"的坏结果。
                # 不拼接、明确记录：长度不足是**可见的**缺陷，
                # 而重复正文是**不可见**的缺陷（字数看着够）。
                problems.append(
                    "续写返回的是重新开头的内容（与已写正文大段重合）—— "
                    "已丢弃该次续写，保留原稿以免正文重复"
                )
                break
            prose = f"{prose}\n\n{tail}"
            continued = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("chapter continue failed: %s", exc)
            problems.append(f"续写调用失败：{exc}")
            break

    chars = count_chars(prose)

    # ── 长度问题只记录，不截断 ──
    # 截断会切断句子并丢掉章末钩子 —— 那比超长更伤（钩子是下一章的入口）。
    if chars < lo:
        problems.append(f"字数不足：{chars} < {lo}（已尝试续写）")
    if chars > hi:
        problems.append(f"字数超上限：{chars} > {hi}（视为注水，交由审校判定）")

    if not prose:
        return WriteResult(
            llm_calls=calls, ok=False, problems=problems, error="正文为空"
        )

    return WriteResult(
        prose=prose,
        prose_hash=content_hash(prose),
        chars=chars,
        llm_calls=calls,
        continued=continued,
        problems=problems,
        ok=True,
    )


def persist_prose(store: ArtifactStore, book_id: int, global_no: int, prose: str) -> str:
    """落盘正文，返回产物 key。

    ★ 大产物**不入图 state**（只有 ref + hash + 字数）——
    LangGraph checkpoint 会完整序列化每次节点执行的 state，
    官方承认 200 轮可达 5.3 GB。
    """
    ref = ArtifactStore.prose_key(book_id, global_no)
    store.write_text(ref, prose)
    return ref.key


def load_prose(store: ArtifactStore, book_id: int, global_no: int) -> str:
    """读正文。**不存在则抛 `ArtifactNotFound`**（不返回空串）。

    旧项目 `read_chapter` 静默返回空串 → 正文丢失不报错。
    """
    return store.read_text(ArtifactStore.prose_key(book_id, global_no))


__all__ = [
    "REFUSAL_PATTERNS",
    "CONTINUE_INSTRUCTION",
    "WriteResult",
    "render_plot_detail",
    "render_health_beats",
    "render_state",
    "assemble_context",
    "detect_refusal",
    "count_chars",
    "looks_like_restart",
    "write_chapter",
    "persist_prose",
    "load_prose",
    "BeatType",
]
