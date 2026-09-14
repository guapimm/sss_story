"""节点：世界观圣经生成（P3-2，人工确认点 C-1）。

## 为什么圣经是"稳定前缀"的第一块

`infra/llm/prompt_builder.py` 的三层设计把圣经放进**稳定前缀**：
全书不变 → 100% 命中缓存。旧项目的单点最大破坏正是这里 ——
`memory.py:1624-1629` 的 `bible_inject.select` 用 `_bible_query = f"{plot} {title}"`
做检索 query，**逐章不同** → 全书最稳的圣经前缀**在第一个块就分叉**
→ 这是命中率卡在 33~44% 的直接原因之一。

因此本节点的产出必须：
  · **定稿后不再改**（改了等于全书缓存失效 + 已写章节的世界观失效）
  · 落盘为 `bible.md`，由 `books.bible_ref` / `bible_hash` 索引
  · **不注入任何逐章变化的内容**

## 旧项目的另一处教训

`books.bible` 列在重开生成时被清空，且新书只写 `bible_path`
→ **每章 L0 世界观圣经静默为空**、世界观/境界/纪元约束失效。

本设计：**只存 ref + hash**，正文在文件里（`ArtifactStore`），
读取失败**抛错**（旧项目 `read_chapter` 静默返回空串）。

## 零 LLM 校验（先跑）

圣经的校验是**结构性**的，不需要 LLM：
  · 必填段落齐全（世界观 / 力量体系 / 硬规则 / 术语）
  · 硬规则数量下限（可被后续 Canon Violation 检测引用）
  · 长度下限（太薄的圣经撑不起长篇）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.llm.schemas import BibleOut
from storyforge.infra.storage.artifacts import ArtifactStore, content_hash

logger = logging.getLogger(__name__)


BIBLE_ARCHITECT_SYSTEM = """你是长篇小说的世界观架构师。为给定的主题与题材构建世界观圣经。

圣经将在**全书每一章**被注入给写作模型，因此它必须：
1. **精炼**：只写影响全局的设定，不写具体情节；
2. **自洽**：力量体系、硬规则、时代背景互不矛盾；
3. **可引用**：硬规则要能作为后续「设定违反」检测的依据。

## 各部分要求

- `worldview`：世界的结构与基本样貌（地理格局、社会形态、当前局势）。300~800 字。
- `power_system`：力量/能力体系的**层级与边界**——
  有哪些层次、如何晋升、代价是什么、什么做不到。这是防止后期战力崩坏的关键。
- `rules`：**硬规则清单**（8~15 条）。每条一句，是**不可违反**的世界法则。
  例：「规则一旦写入手册便不可擦除，只能覆盖」「午夜后应答唤名者视为交接完成」。
  不要写「主角很勇敢」这类人物描述——那是人物设定不是世界规则。
- `era`：时代与地域背景（年代感、技术水平、社会制度）。
- `tone`：整体基调与文风（如「冷峻写实的中式规则怪谈」）。
- `terms`：**术语与专名对照**（6~15 条）。格式「世界内说法——含义」。
  用于保证全书用词一致。
- `title`：书名。

**只输出 JSON**，不要任何解释或代码块标记。"""


#: 圣经的必填段落下限（零 LLM 校验）
_MIN_WORLDVIEW_CHARS = 200
_MIN_RULES = 6
_MIN_TERMS = 4


@dataclass
class BibleResult:
    """圣经生成结果。"""

    bible: BibleOut | None
    bible_ref: str
    bible_hash: str | None
    violations: list[str] = field(default_factory=list)
    llm_calls: int = 0
    ok: bool = False
    error: str | None = None


def validate_bible(bible: BibleOut) -> list[str]:
    """零 LLM 的结构校验。返回问题列表（空 = 通过）。

    与旧项目 `verifier` 的区别：那些是**语义**检查（且空输出率 62.3%）；
    本函数只做**确定性可判定**的结构检查，语义问题留给审校节点。
    """
    issues: list[str] = []

    if not bible.title.strip():
        issues.append("缺书名")
    if len(bible.worldview.strip()) < _MIN_WORLDVIEW_CHARS:
        issues.append(
            f"世界观过薄（{len(bible.worldview.strip())} 字 < {_MIN_WORLDVIEW_CHARS}）"
            f"—— 长篇需要足够的设定密度"
        )
    if not bible.power_system.strip():
        issues.append(
            "缺力量/能力体系 —— 这是防止后期战力崩坏的关键"
            "（旧项目实测后期「力量层次」约束失效）"
        )
    if len(bible.rules) < _MIN_RULES:
        issues.append(
            f"硬规则只有 {len(bible.rules)} 条（要求 ≥{_MIN_RULES}）"
            f"—— 硬规则是后续「设定违反」检测的依据"
        )
    if len(bible.terms) < _MIN_TERMS:
        issues.append(
            f"术语对照只有 {len(bible.terms)} 条（要求 ≥{_MIN_TERMS}）"
            f"—— 术语是全书用词一致的基础"
        )
    # 规则不得是人物描述（常见的模型偏题）
    person_markers = ("勇敢", "善良", "聪明", "坚强", "主角", "他", "她")
    bad_rules = [
        r for r in bible.rules
        if len(r.strip()) < 6 or any(m in r for m in person_markers)
    ]
    if bad_rules:
        issues.append(
            f"{len(bad_rules)} 条「硬规则」不像世界法则（过短或含人物描述）："
            f"{bad_rules[:2]}"
        )
    return issues


def bible_to_markdown(bible: BibleOut) -> str:
    """渲染为 markdown（注入 writer prompt 用的就是这份文本）。

    ⚠️ **不得**在这里插入任何逐章变化的内容（章号/时间戳/状态）——
    它是稳定前缀（`PromptLayers.stable`），必须逐字节稳定。
    """
    lines = [f"# {bible.title}", ""]
    if bible.tone:
        lines += [f"**基调**：{bible.tone}", ""]
    lines += ["## 【世界观】", bible.worldview.strip(), ""]
    if bible.era:
        lines += ["## 【纪元与年代】", bible.era.strip(), ""]
    if bible.power_system:
        lines += ["## 【力量/规则体系】", bible.power_system.strip(), ""]
    if bible.rules:
        lines += ["## 【世界硬规则】"]
        lines += [f"{i}. {r.strip()}" for i, r in enumerate(bible.rules, start=1)]
        lines += [""]
    if bible.terms:
        lines += ["## 【术语与专名】"]
        lines += [f"- {t.strip()}" for t in bible.terms]
        lines += [""]
    return "\n".join(lines)


def persist_bible(store: ArtifactStore, book_id: int, bible: BibleOut) -> tuple[str, str]:
    """落盘圣经，返回 `(ref, hash)`。

    `hash` 用于两个用途：
      1. 检测圣经是否被改动（改了就应视为**全书级别的失效**，需人工确认）
      2. 作为稳定前缀的一部分参与缓存分析
    """
    ref = ArtifactStore.bible_key(book_id)
    text = bible_to_markdown(bible)
    store.write_text(ref, text)
    return ref.key, content_hash(text)


def load_bible_text(store: ArtifactStore, book_id: int) -> str:
    """读取圣经文本。**不存在则抛错**（旧项目静默返回空 → L0 约束失效）。"""
    return store.read_text(ArtifactStore.bible_key(book_id))


async def build_bible(
    client: LLMClient,
    *,
    book_id: int,
    topic: str,
    genre: str,
    max_rounds: int = 2,
    idempotency_key: str | None = None,
) -> BibleResult:
    """生成世界观圣经（零 LLM 校验失败则带问题清单重试）。

    人工确认点 **C-1**：本节点产出后应暂停等人工确认（决策 D-14）。
    确认通过后再进入全卷台阶规划（台阶依赖圣经）。
    """
    user = f"【主题】{topic}\n【题材】{genre}\n\n请构建世界观圣经。"

    ctx = CallContext(node="bible_architect", book_id=book_id,
                      idempotency_key=idempotency_key)
    calls = 0
    last_issues: list[str] = []

    for attempt in range(max_rounds + 1):
        try:
            bible, call = await client.complete_structured(
                node="bible_architect",
                system=BIBLE_ARCHITECT_SYSTEM,
                user=user,
                schema=BibleOut,
                context=ctx,
            )
            # ★ 幂等命中不计入 llm_calls —— 它没有真正调用 provider。
            # 这个计数会进入观测报告，把缓存命中算成真实调用会**虚高成本**。
            if not call.from_cache:
                calls += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("bible LLM failed (attempt %d): %s", attempt + 1, exc)
            return BibleResult(
                bible=None, bible_ref="", bible_hash=None,
                violations=last_issues, llm_calls=calls, ok=False,
                error=f"圣经生成调用失败：{exc}",
            )

        last_issues = validate_bible(bible)
        if not last_issues:
            return BibleResult(
                bible=bible, bible_ref="", bible_hash=None,
                violations=[], llm_calls=calls, ok=True,
            )

        logger.info(
            "bible validation failed (round %d/%d): %s",
            attempt + 1, max_rounds, last_issues,
        )
        if attempt >= max_rounds:
            break
        # 增量修正：只把问题清单发回去，不重发全部上下文
        user = (
            f"【主题】{topic}\n【题材】{genre}\n\n"
            f"【上一次的圣经未通过结构校验，请针对下列问题修正后重新输出完整 JSON】\n"
            + "\n".join(f"- {i}" for i in last_issues)
            + "\n\n其余部分保持原样。"
        )

    return BibleResult(
        bible=None, bible_ref="", bible_hash=None,
        violations=last_issues, llm_calls=calls, ok=False,
        error=f"圣经在 {max_rounds + 1} 次尝试后仍未通过校验",
    )


__all__ = [
    "BIBLE_ARCHITECT_SYSTEM",
    "BibleResult",
    "validate_bible",
    "bible_to_markdown",
    "persist_bible",
    "load_bible_text",
    "build_bible",
]
