"""learn 阶段的知识点与平替修辞生成节点（阶段 2-3 收尾）。

## 为什么在 `graph/nodes/` 而不是 `modes/`

分层约束：`modes/` **不得 import `infra.llm`**（契约测试强制）——
它回答"这个模式给什么约束"，不负责"怎么调模型"。
而**调模型**是 `graph/nodes/` 的职责（与 `extractors.py` 同级）。

`modes/protocols.py` 的 `LearningMapProvider` 是**契约**；
本模块是它的**实现**（用 LLM 的那个）。

## 三个节点（对照旧项目 `agents/researcher.py` 的五合一流程）

| 本模块 | 旧项目 | 作用 |
|---|---|---|
| `generate_learning_map` | `_gen_map`（`:217-245`） | 研究资料 → 学习地图 |
| `check_gaps` | `_gap_fill` 前半（`:344-373`） | 找盲区方向 |
| `generate_term_mappings` | `templates/scenes.py` 的 `mapping_rules` | 术语 → 世界内说法 |

★ 旧项目还有 `QueryPlannerAgent`（查询规划）与 `VerifyAgent`（逐点核验）——
前者已由阶段 2-2 的 `ANGLES` 角度模板覆盖，后者（事实验证）
属 `FactVerifier`，**尚未实现**（诚实标注，见交接文档 §10）。

## 分档规模（逐字来自旧项目 `RESEARCH_PROFILE`）

    fast     : 每模块 4 点，2~3 模块
    standard : 每模块 6 点，3~5 模块
    grand    : 每模块 8 点，6~10 模块
    ultra    : 每模块 10 点，10~14 模块

★ 这些数字**不是**四舍五入凑的：它们与阶段 2-2 的
`points_per_scale`（8/25/60/150）配对，构成"研究目标 ↔ 地图规模"的一致口径。
"""

from __future__ import annotations

from storyforge.infra.llm.client import CallContext
from storyforge.infra.llm.learning_schemas import (
    GAP_CHECK_SYSTEM,
    LEARNING_MAP_SYSTEM,
    MAPPING_SYSTEM,
    VERIFY_SYSTEM,
    GapCheckOut,
    LearningMapOut,
    TermMappingBatchOut,
    VerificationOut,
)
from storyforge.infra.llm.prompt_builder import PromptLayers

#: 分档规模 —— 逐字来自旧项目 `agents/researcher.py:28-33` 的 `RESEARCH_PROFILE`
#:
#: ★ 与 `ResearchSettings.points_per_scale` 配对：研究要搜够，
#: 地图才铺得开；两者口径不一致会让"搜了很多但地图很小"（或反之）。
SCALE_PROFILE: dict[str, dict[str, object]] = {
    "fast": {"points_per_module": 4, "modules": "2~3"},
    "standard": {"points_per_module": 6, "modules": "3~5"},
    "grand": {"points_per_module": 8, "modules": "6~10"},
    "ultra": {"points_per_module": 10, "modules": "10~14"},
}


def _profile_line(scale: str) -> str:
    """把分档规模写成 prompt 里的一句约束。"""
    p = SCALE_PROFILE.get(scale) or SCALE_PROFILE["standard"]
    return (
        f"【篇幅档位：{scale}】"
        f"每模块知识点 ≤{p['points_per_module']} 个，"
        f"模块数 {p['modules']} 个。"
    )


def _snippet_block(snippets: list[dict], *, cap: int = 60) -> str:
    """把研究片段渲染成 prompt 块。

    `cap` 限制条数（旧项目 `RESEARCH_PROFILE` 的 `snippet_cap` 是 40~90）——
    不限制会让 grand/ultra 档的 prompt 无限膨胀。
    """
    lines: list[str] = []
    for i, s in enumerate(snippets[:cap], 1):
        title = str(s.get("title", "")).strip()
        text = str(s.get("text", "")).strip()
        url = str(s.get("url", "")).strip()
        head = f"[{i}] {title}" + (f"（{url}）" if url else "")
        lines.append(f"{head}\n{text[:600]}")
    return "\n\n".join(lines)


def _layers(system: str, user: str) -> PromptLayers:
    """两层 prompt：system 稳定（可缓存），user 易变。"""
    layers = PromptLayers()
    layers.stable.add("system", system)
    layers.volatile.add("input", user)
    return layers


async def generate_learning_map(
    client,
    *,
    topic: str,
    genre: str,
    scale: str,
    snippets: list[dict],
    research_shortfall: str = "",
    book_id: int = 0,
    idempotency_key: str | None = None,
) -> tuple[LearningMapOut | None, int]:
    """研究资料 → 学习地图。返回 `(结果, 实际调用次数)`。

    ★ `research_shortfall` 会被**明确告诉模型**：研究没搜够时说清楚，
    比让模型自己猜"资料为什么这么少"更可能得到诚实的（小）地图，
    而不是编造内容凑数。
    """
    user_parts = [
        f"学习主题：{topic}",
        f"目标小说题材：{genre}",
        _profile_line(scale),
    ]
    if research_shortfall:
        user_parts.append(
            f"⚠️ 研究资料不足：{research_shortfall}\n"
            f"（请**只依据现有资料**给地图；资料没覆盖的方向不要编造）"
        )
    user_parts.append(f"联网研究资料（{len(snippets)} 条）：\n"
                      + (_snippet_block(snippets) or "（无资料）"))
    user_parts.append("请产出结构化学习地图。")

    layers = _layers(LEARNING_MAP_SYSTEM, "\n\n".join(user_parts))
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="learning_map",
        system=messages["system"],
        user=messages["user"],
        schema=LearningMapOut,
        prompt_prefix=layers.cacheable_prefix(),
        context=CallContext(
            node="learning_map", book_id=book_id or None,
            idempotency_key=idempotency_key,
        ),
    )
    return out, (0 if call.from_cache else 1)


async def check_gaps(
    client,
    *,
    topic: str,
    map_out: LearningMapOut,
    max_gaps: int = 3,
    book_id: int = 0,
    idempotency_key: str | None = None,
) -> tuple[GapCheckOut | None, int]:
    """缺口自检：找出地图盲区方向（**不补全**，补全由调用方决定）。

    旧项目 `_gap_fill` 是两段式：先 `_GapChecker` 找方向，
    再 `_MapFiller` 补模块。本函数对应**第一段** ——
    补全要不要做由调用方按成本决定（stage 2 的 `max_rounds` 思路一致）。
    """
    summary = "\n".join(
        f"- {m.name}：{'、'.join(p.name for p in m.knowledge_points)}"
        for m in map_out.modules
    )
    user = (
        f"学习主题：{topic}\n"
        f"【现有学习地图模块】\n{summary}\n\n"
        f"请找出最多 {max_gaps} 个知识盲区方向。"
    )
    layers = _layers(GAP_CHECK_SYSTEM, user)
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="learning_gap_check",
        system=messages["system"],
        user=messages["user"],
        schema=GapCheckOut,
        prompt_prefix=layers.cacheable_prefix(),
        context=CallContext(
            node="learning_gap_check", book_id=book_id or None,
            idempotency_key=idempotency_key,
        ),
    )
    return out, (0 if call.from_cache else 1)


async def verify_learning_map(
    client,
    *,
    topic: str,
    points: list[tuple[str, str]],
    snippets: list[dict],
    book_id: int = 0,
    idempotency_key: str | None = None,
) -> tuple[VerificationOut | None, int]:
    """核验知识点**是否被研究资料支撑**（方案 B1，草稿 §4）。

    ## 输入为什么是 `(名字, 说明)` 而不是 `LearningMapOut`

    核验侧**不该看到生成过程** —— 只给「研究资料 + 待核验的知识点」。
    传 `LearningMapOut` 会把模块结构、difficulty、dependencies 一并带过去，
    等于把生成者的思路喂给核验者（削弱独立性）。

    ## 为什么资料要**重新编号**

    ★ 这是本函数最容易写错的地方：生成侧 prompt 给资料编了 `[N]` 号，
    核验侧的 prompt 里却是**另一段文本**。若两边都用"第 N 条"而编号规则
    不一致，模型回的 `evidence_index` 会**指向错误的资料** ——
    而且是静默错（看起来有编号、其实对不上）。

    故这里用 `_snippet_block` **重新渲染并重新编号**，
    保证「核验 prompt 里的编号」与「模型回的编号」在同一坐标系里。

    ## 返回

    `(核验结果, 实际调用次数)`；结果可为 `None`（空输出，由调用方决定处置）。
    """
    numbered = _snippet_block(snippets)
    point_lines = "\n".join(
        f"{i}. {name}" + (f" —— {summary}" if summary else "")
        for i, (name, summary) in enumerate(points, 1)
    )
    user = (
        f"学习主题：{topic}\n\n"
        f"## 研究资料\n\n{numbered or '（无资料）'}\n\n"
        f"## 待核验的知识点（共 {len(points)} 条）\n\n{point_lines}\n\n"
        f"请逐条判定，共输出 {len(points)} 条 verdicts。"
    )
    layers = _layers(VERIFY_SYSTEM, user)
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="learning_verify",
        system=messages["system"],
        user=messages["user"],
        schema=VerificationOut,
        prompt_prefix=layers.cacheable_prefix(),
        context=CallContext(
            node="learning_verify", book_id=book_id or None,
            idempotency_key=idempotency_key,
        ),
    )
    return out, (0 if call.from_cache else 1)


async def generate_term_mappings(
    client,
    *,
    topic: str,
    genre: str,
    terms: list[str],
    flow_names: list[str] | None = None,
    seed_examples: list[dict] | None = None,
    book_id: int = 0,
    idempotency_key: str | None = None,
) -> tuple[TermMappingBatchOut | None, int]:
    """生成平替修辞（术语 → 世界内说法）。

    ## 为什么把 `seed_examples` 放进 prompt

    用户决策"流派根据已有的来" —— 旧项目 10 条 `mapping_rules` 是
    **已验证的映射范式**，作为 few-shot 能显著稳定输出的粒度与风格。

    ⚠️ 但 prompt 里明确写了"示例只示范粒度与风格，不要照抄"，
    且 `LearningMapOut` 的校验会拒绝"说法与原术语相同"的空平替 ——
    防的是把种子直接当结果返回（`scenes.py:14` 明写"必须按本书自创"）。
    """
    user_parts = [
        f"学习主题：{topic}",
        f"目标小说题材：{genre}",
    ]
    if flow_names:
        user_parts.append(
            "本书选用的流派（说法的风格应与之一致）：" + "、".join(flow_names)
        )
    user_parts.append(
        "需要平替的术语（来自学习地图）：\n"
        + "\n".join(f"- {t}" for t in terms)
    )
    if seed_examples:
        import json

        user_parts.append(
            "【参考示例（只示范粒度与风格，**不要照抄**）】\n"
            + json.dumps(seed_examples, ensure_ascii=False, indent=1)
        )
    user_parts.append("请为上述术语产出平替修辞。")

    layers = _layers(MAPPING_SYSTEM, "\n\n".join(user_parts))
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="term_mapping",
        system=messages["system"],
        user=messages["user"],
        schema=TermMappingBatchOut,
        prompt_prefix=layers.cacheable_prefix(),
        context=CallContext(
            node="term_mapping", book_id=book_id or None,
            idempotency_key=idempotency_key,
        ),
    )
    return out, (0 if call.from_cache else 1)


__all__ = [
    "SCALE_PROFILE",
    "generate_learning_map",
    "check_gaps",
    "verify_learning_map",
    "generate_term_mappings",
]
