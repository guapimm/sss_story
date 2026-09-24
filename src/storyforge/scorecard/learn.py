"""learn（知识小说）专属打分卡 —— 维度集合与 story **不同**（阶段 2-5）。

## 为什么不能用 story 的打分卡（实测，不是调参问题）

用 story 卡评一篇**标准说明文**（`scripts/diag_scorecard_learn.py`）：

    score=0.51  passed=False
    triggers=[golden_three, dialogue_low, sentence_uniform, too_short, low_score]

其中 **`dialogue_low` 是硬触发** —— 而教学章「**不要对话**」才是**正确**写法。
`综上所述` 又被 `CLICHES:86` 当套话，而它是**教学标准连接词**。

⇒ **维度集合本身错配**：不是阈值松紧问题，是"测什么"错了。

## 维度集合（用户 2026-09-24 拍板：全部 10 项）

| # | 维度 | 教学含义 | 零 LLM 可测？ |
|---|---|---|---|
| 1 | `knowledge_density` | 知识密度（单位字数承载知识点） | ✅ |
| 2 | `term_substitution` | **平替修辞正确率**（正文不得直出原术语） | ✅ 拿映射表扫正文 |
| 3 | `explanation_depth` | 解释充分性（讲透 vs 只提名词） | ✅ 启发式 |
| 4 | `progression` | 递进连贯性（从已知引到未知） | ✅ 用 `dependencies` |
| 5 | `intra_redundancy` | 章内冗余 | ✅ **复用 story 度量原语** |
| 6 | `cross_chapter_overlap` | 跨章重叠 | ✅ **复用** |
| 7 | `chars` | 篇幅 | ✅ **复用**（阈值重定） |
| 8 | `review_injection` | **复习点注入**（到期知识点是否真讲了） | ✅ **结合 2-4 教学账本** |
| 9 | `dialogue_ratio` | 对话占比 —— ★ **上限**而非下限 | ✅ |
| 10 | `simile_count` | 比喻数量 —— ★ **上限**而非"AI 味" | ✅ |
| 11 | `ending_hook` | 章末钩子 —— ★ 教学章**允许**总结收尾 | ✅ |

## ★ 三处与 story **语义相反**的地方（最关键的差异）

1. **对话占比**：story 是「下限 ≥25%」（对话少=不好）；learn 是
   「**上限**」（教学章对话本就少，多到挤占讲解才不好）。
2. **比喻**：story 把比喻当 **AI 味**（用户痛点 3）；learn 里
   **比喻是教学手段**（拿比喻讲抽象概念），只有"过量"才是问题。
3. **章末总结**：story 把总结式收尾列为可疑（`SUMMARY_ENDINGS`）；
   learn 的**总结是教学必需**（"回顾本讲"），不判罚。

★ 第 3 条的落地方式：learn **不**复用 story 的 `ending_not_omen`
判定（那个把 `SUMMARY` 也列入可疑），改为只排除
`TIME_SKIP`（时间硬切）—— 教学章用"下一讲"承接是正确的。

## 分层与依赖

本模块**只做维度声明与阈值**，不做度量（度量复用 `literal.py` /
`structural.py` 的原语）+ 不打分（打分在 `aggregate.evaluate`）。
零 LLM（契约规则 2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storyforge.scorecard.dimensions import DimensionSpec

# --------------------------------------------------------------------------- #
# 阈值（learn 专属）
# --------------------------------------------------------------------------- #


@dataclass
class LearnThresholds:
    """learn 打分卡阈值。

    ## 标定依据（★ 不是拍脑袋）

    用户决策：**用书 993 的真实产物标定**（该书有 10 个真实知识点
    + 10 条平替修辞 + 12 条真实资料 + 准确性核验）。

    ⚠️ **诚实标注**：书 993 只有**学习地图**（知识点与平替修辞表），
    **没有教学章正文**（正文需要步骤 ⑥ 接入 story 规划链路之后才能产出）。
    因此：

      · 「平替修辞」「复习点」两类的阈值可用**地图数据**直接标定 ✅
      · 「知识密度」「解释充分性」等**正文类**阈值目前只能取
        **领域常识值**，并标注为"待真实教学章产出后复核" ⬜

    ★ 这与 story 卡的做法一致：story 的阈值也不是一次定死的，
    而是先用旧项目 47 章分布标定、再逐期抬升（`Thresholds` 的注释）。

    ## 标定状态（★ 诚实标注，勿把"常识值"当"标定值"）

    | 阈值组 | 状态 | 依据 |
    |---|---|---|
    | 平替修辞（`target_substitution_rate` / `hard_bare_term_hits`） | ✅ **已按真实产物标定** | 书 993 的 10 条真实映射；术语 5~9 字（`scripts/calibrate_learn_thresholds.py`） |
    | 冗余 / 重叠 / 篇幅 | ✅ **有依据** | 复用 story 的度量原语，按"教学章允许复述"放宽（story 基线 p50 可查） |
    | 对话上限 / 比喻上限 | ✅ **有依据** | 语义反转自 story 的下限/上限（story: 对话 ≥25%；learn: ≤35%） |
    | 知识密度 / 解释充分性 / 递进 / 复习注入 | ⬜ **待真实教学章复核** | ★ **目前是领域常识值，不是标定值** —— 标定需要**教学章正文**，而正文依赖步骤 ⑥（learn 接入 story 规划链路）。见 `LEARN_DIMENSIONS` 的 `note` |

    ★ 为什么明写"未标定"：本项目记录过「没有依据的阈值会退化成拍脑袋」。
    **"看起来有依据"比"明说没依据"更危险** —— 后者会被人复核，
    前者会被当成已验证而一路沿用。

    ## 双层设计（沿用 story 的教训）

    单层阈值实测会让 **47/47 章全部触发**（story 的坑）→ 闸门退化。
    故同样分 `hard_*`（明确缺陷）与 `target_*`（目标水位）。
    """

    # ── 知识密度 ──
    #: 每千字至少承载的知识点数（低于此值说明在灌水）
    target_points_per_1k: float = 1.0
    #: 硬下限：整章只提了 1 个知识点且不足千字 → 大概率是空章
    hard_points_per_1k: float = 0.3
    # ── 平替修辞正确率 ──
    #: 已确认映射的**命中率**下限（正文提到术语时，有多少比例用了世界内说法）
    target_substitution_rate: float = 0.9
    #: ★ 硬门禁：**原术语直出**次数。教学章直出「函数」「递归」
    #: 就是**出戏**（用户做 L-2 逐条确认的全部意义所在）。
    hard_bare_term_hits: int = 3

    # ── 解释充分性 ──
    #: 知识点被"讲透"的比例（有解释句 vs 只提名）
    target_explained_ratio: float = 0.7
    hard_explained_ratio: float = 0.4

    # ── 递进连贯性 ──
    #: 满足 `dependencies` 顺序的知识点比例
    target_progression: float = 0.8
    hard_progression: float = 0.5

    # ── 复用 story 的度量原语（阈值重定）──
    #: 章内冗余（教学章允许小结重复，故上限比 story 宽）
    target_intra_redundancy: float = 0.12
    hard_intra_redundancy: float = 0.20
    #: 跨章重叠（教学章会复述上一讲，故比 story 宽）
    target_cross_overlap: float = 0.10
    hard_cross_overlap: float = 0.18
    #: 篇幅（教学章可以比小说章短，但太短讲不透）
    target_min_chars: int = 2000
    hard_min_chars: int = 1200

    # ── ★ 上限型（与 story 语义相反）──
    #: 对话占比**上限**（教学章对话少是正常的，多到挤占讲解才不好）
    target_dialogue_ceiling: float = 0.35
    hard_dialogue_ceiling: float = 0.55
    #: 比喻数量**上限**（比喻是教学手段，过量才成问题）
    target_simile_ceiling: int = 8
    hard_simile_ceiling: int = 15

    # ── 复习点注入 ──
    #: 到期复习点的注入率（结合 2-4 教学账本）
    target_review_injection: float = 0.8
    hard_review_injection: float = 0.5

    # ── 章末 ──
    #: ★ learn **不**惩罚总结式收尾（教学必需）；只排除时间硬切
    forbid_time_skip_ending: bool = True

    # ── 总分 ──
    #: 参考 story 的 0.62（比旧基线中位数明显更差）
    score_threshold: float = 0.62

    #: ★ learn **没有**「黄金三章」概念（那是网文留存策略）。
    #: 一期不做无条件送审；教学章的质量门禁靠分数与硬阈值。
    always_review_chapters: tuple[int, ...] = ()


# --------------------------------------------------------------------------- #
# 维度表
# --------------------------------------------------------------------------- #
#: ★ 用户 2026-09-24 拍板：全部 10 项（含被我初判"排除"的对话/比喻/钩子，
#: 但**语义已重定义**为上限型 —— 见本模块 docstring 的三处相反语义）。
LEARN_DIMENSIONS: tuple[DimensionSpec, ...] = (
    # ── 教学专属（learn 的核心，story 完全没有）──
    DimensionSpec(
        name="knowledge_density", metric="knowledge_density", direction="ge",
        target="target_points_per_1k", weight=1.5,
        note="每千字承载的知识点数（防灌水）",
    ),
    DimensionSpec(
        name="term_substitution", metric="substitution_rate", direction="ge",
        target="target_substitution_rate", weight=2.0,
        note="★ 平替修辞命中率（正文用世界内说法而非原术语）",
    ),
    DimensionSpec(
        name="explanation_depth", metric="explained_ratio", direction="ge",
        target="target_explained_ratio", weight=1.5,
        note="知识点被讲透的比例（防列举式教学）",
    ),
    DimensionSpec(
        name="progression", metric="progression_rate", direction="ge",
        target="target_progression", weight=1.2,
        note="按 dependencies 从已知引到未知",
    ),
    DimensionSpec(
        name="review_injection", metric="review_injection_rate", direction="ge",
        target="target_review_injection", weight=1.2,
        note="★ 到期复习点被真正讲到（结合教学账本）",
    ),
    # ── 复用 story 的通用文本度量（阈值重定）──
    DimensionSpec(
        name="intra_redundancy", metric="intra_redundancy", direction="le",
        target="target_intra_redundancy", weight=1.0,
        note="章内冗余（教学章允许小结，上限放宽）",
    ),
    DimensionSpec(
        name="cross_chapter_overlap", metric="cross_chapter_overlap",
        direction="le", target="target_cross_overlap", weight=1.0,
        note="跨章重叠（教学章会复述上一讲，上限放宽）",
    ),
    DimensionSpec(
        name="chars", metric="chars", direction="ge",
        target="target_min_chars", weight=1.2,
        note="篇幅（太短讲不透）",
    ),
    # ── ★ 上限型（与 story 语义相反）──
    DimensionSpec(
        name="dialogue_ceiling", metric="dialogue_ratio", direction="le",
        target="target_dialogue_ceiling", weight=0.8,
        note="★ 对话占比**上限** —— 教学章少对话是正确的",
    ),
    DimensionSpec(
        name="simile_ceiling", metric="simile_count", direction="le",
        target="target_simile_ceiling", weight=0.6,
        note="★ 比喻**上限** —— 比喻是教学手段，过量才是问题",
    ),
    DimensionSpec(
        name="no_time_skip", metric="no_time_skip_ending", direction="flag",
        weight=0.5,
        note="章末不用时间硬切（★ learn **不**惩罚总结式收尾）",
    ),
)


#: learn 的**硬触发**（对应 `TriggerReason` 里 learn 可用的项）。
#:
#: ★ 刻意**不含** `DIALOGUE_LOW` / `GOLDEN_THREE` / `ENDING_OMEN`：
#:   · `DIALOGUE_LOW` —— 教学章对话少是**正确**写法（实测误杀源）
#:   · `GOLDEN_THREE` —— 网文留存策略，教学章无此概念
#:   · `ENDING_OMEN` —— 教学章的总结收尾是**必需**，不是假钩子
LEARN_TRIGGERS: tuple[str, ...] = (
    "cross_overlap",
    "intra_redundancy",
    "too_short",
    "bare_term",          # ★ learn 专属：原术语直出（出戏）
    "term_substitution",  # ★ learn 专属：平替命中率过低
    "low_density",        # ★ learn 专属：知识密度过低（灌水）
    "low_score",
)


@dataclass
class LearnMetrics:
    """learn 章正文的度量输入（由调用方算好传入，便于单测）。"""

    #: 正文中**直出的原术语**次数（应为 0 或极少）
    bare_term_hits: int = 0
    #: 已确认映射命中数 / 应命中数 → 命中率
    substitution_hits: int = 0
    substitution_expected: int = 0
    #: 提到的知识点数（用于知识密度）
    points_mentioned: int = 0
    #: 知识点里"被讲透"（有解释句）的数量
    points_explained: int = 0
    #: 满足依赖顺序的知识点数 / 可判断数
    progression_ok: int = 0
    progression_expected: int = 0
    #: 到期复习点被讲到的数 / 到期总数（结合 2-4 教学账本）
    review_injected: int = 0
    review_due: int = 0
    #: 章末是否时间硬切
    starts_or_ends_with_time_skip: bool = False

    # -- 派生（全部零 LLM）------------------------------------------------

    @property
    def substitution_rate(self) -> float | None:
        """平替命中率。无应命中项 → `None`（**无法评估**，不是 100%）。

        ★ 返回 `None` 而非 1.0：没有可评估项 ≠ 全对。
        这与 `VerificationOut.accuracy()` 的空结果口径一致
        （本项目反复吃亏于"空结果当成通过"的假绿）。
        """
        if self.substitution_expected <= 0:
            return None
        return self.substitution_hits / self.substitution_expected

    @property
    def explained_ratio(self) -> float | None:
        if self.points_mentioned <= 0:
            return None
        return self.points_explained / self.points_mentioned

    @property
    def progression_rate(self) -> float | None:
        if self.progression_expected <= 0:
            return None
        return self.progression_ok / self.progression_expected

    @property
    def review_injection_rate(self) -> float | None:
        if self.review_due <= 0:
            return None
        return self.review_injected / self.review_due

    @property
    def no_time_skip_ending(self) -> bool:
        return not self.starts_or_ends_with_time_skip


def knowledge_density(points_mentioned: int, chars: int) -> float | None:
    """每千字承载的知识点数。

    `chars <= 0` → `None`（无法评估）；不是 0.0 —— 空文本的"密度"
    没有意义，报 0 会让它被当成"密度极低"（另一种误判）。
    """
    if chars <= 0:
        return None
    return points_mentioned / (chars / 1000.0)


def count_bare_terms(
    prose: str, mappings: list[tuple[str, str]],
) -> tuple[int, list[str]]:
    """统计正文里**直出的原术语**（返回 `(次数, 命中的术语)`）。

    ## 检测口径（依据真实产物标定，`scripts/calibrate_learn_thresholds.py`）

    实测书 993 的术语是 **5~9 字**的知识短语（「调膏与七汤注水」），
    不是 2~5 字单词。这带来两个**必须处理**的细节：

    1. **先剥掉"世界内说法"再匹配** —— 否则说法本身若含术语会自我误报。
       （本次实测 10/10 条都不含，但那是运气；别的主题可能含。）
    2. **只匹配术语整体，不做部分匹配** —— 正文写「候汤最难」是在讲
       这个知识点（术语是「候汤与熁盏」），**不应**判为直出。
       故这里是**全串 `in` 判断**，不是分词/模糊匹配。

    ★ 为什么这条维度值 2.0 权重（全表最高）：它是用户做 L-2
    **逐条确认**的全部意义 —— 确认了映射却在正文里直出原术语，
    等于确认流程白做，且读者会看到「函数」「递归」这种**出戏**词。
    """
    if not prose:
        return 0, []
    text = prose
    # ① 先把"世界内说法"替换成占位符 —— 防说法含术语时自我误报
    for term, replacement in mappings:
        if replacement and replacement in text:
            text = text.replace(replacement, "\u0000")
    # ② 再找剩余的术语整体
    hits: list[str] = []
    for term, _replacement in mappings:
        if not term:
            continue
        n = text.count(term)
        if n:
            hits.extend([term] * n)
    return len(hits), hits


def count_term_usage(
    prose: str, mappings: list[tuple[str, str]],
) -> tuple[int, int]:
    """统计"该用世界内说法的地方，用对了多少"（返回 `(命中, 应有)`）。

    ## 口径

    对每条**已确认**映射：
      · 正文出现了 `replacement` → 命中（用对了）✅ +1
      · 正文既没出现 `replacement`、也没出现 `term`
        → **不计入分母**（本章没讲这个知识点，无从判断）
      · 正文出现 `term`（直出）→ 计入分母但**不命中** ❌

    ★ 「没讲就不计入」是刻意的：若把"本章没提到的知识点"也算进分母，
    任何一章的命中率都会被未讲的知识点拉低 —— 那是**分母造假**，
    与 story 卡"47/47 章全触发"是同一类错误（闸门失去筛选意义）。
    """
    hit = expected = 0
    for term, replacement in mappings:
        if not term:
            continue
        used_ok = bool(replacement) and replacement in prose
        used_bare = term in prose
        if used_ok:
            hit += 1
            expected += 1
        elif used_bare:
            expected += 1          # 直出 = 明确没做对
    return hit, expected


def evaluate_learn(
    prose: str,
    *,
    global_no: int,
    metrics: LearnMetrics | None = None,
    prev_texts: list[str] | None = None,
    thresholds: LearnThresholds | None = None,
    dimensions: tuple[DimensionSpec, ...] | None = None,
) -> "object":
    """对一章**教学正文**打分（零 LLM、可复现）。

    ## 与 `aggregate.evaluate` 的关系

    **不修改** `aggregate.evaluate`（它服务 story，行为已被快照钉住）。
    本函数是 learn 的**平行入口**：复用 `literal.analyze` 的度量原语
    （跨章重叠 / 章内冗余 / 对话占比 / 比喻），加上 learn 专属指标
    （知识密度 / 平替命中 / 解释充分性 / 递进 / 复习注入）。

    ★ 为什么平行而不参数化 `evaluate`：两者的**指标来源不同** ——
    story 从 `LiteralMetrics + StructuralMetrics` 取，learn 还要
    `LearnMetrics`（教学专属，story 没有对应概念）。硬塞进同一个
    签名会让 story 侧多一个恒为 `None` 的参数（假的通用性）。

    ## 返回

    `Scorecard`（复用 story 的容器，字段语义相同：dimensions / triggers /
    score / needs_review）—— 这样报告与 CLI 无需分叉。
    """
    from storyforge.scorecard.aggregate import Scorecard, DimensionScore
    from storyforge.scorecard.literal import analyze as analyze_literal
    from storyforge.domain.enums import TriggerReason

    th = thresholds or LearnThresholds()
    specs = dimensions if dimensions is not None else LEARN_DIMENSIONS
    m = metrics or LearnMetrics()
    lit = analyze_literal(prose, prev_texts=prev_texts)

    # ── 维度（按 spec 判定；target 层，不单独触发）──
    dims: list[DimensionScore] = []
    for spec in specs:
        accessor = LEARN_ACCESSORS.get(spec.metric)
        if accessor is None:
            raise KeyError(
                f"learn 维度 {spec.name!r} 引用了未登记的访问器 "
                f"{spec.metric!r}（合法值：{sorted(LEARN_ACCESSORS)}）"
            )
        raw = accessor(m, lit)  # type: ignore[operator]

        if spec.direction == "flag":
            passed = None if raw is None else bool(raw)
            value: float | None = None
        else:
            value = None if raw is None else float(raw)  # type: ignore[arg-type]
            if value is None:
                passed = None
            else:
                limit = float(getattr(th, spec.target)) if spec.target else 0.0
                passed = value >= limit if spec.direction == "ge" else value <= limit

        detail = f"目标 {'≥' if spec.direction == 'ge' else '≤'}"
        if spec.target:
            detail += f"{getattr(th, spec.target)}"
        if spec.note:
            detail += f"；{spec.note}"
        dims.append(DimensionScore(
            name=spec.name, value=value, passed=passed,
            weight=spec.weight, detail=detail,
        ))

    total_weight = sum(d.weight for d in dims) or 1.0
    score = sum(d.earned for d in dims) / total_weight

    # ── 触发（hard 层；刻意不含 DIALOGUE_LOW / GOLDEN_THREE / ENDING_OMEN）──
    triggers: list[TriggerReason] = []
    if (lit.cross_chapter_overlap is not None
            and lit.cross_chapter_overlap > th.hard_cross_overlap):
        triggers.append(TriggerReason.CROSS_OVERLAP)
    if (lit.intra_redundancy is not None
            and lit.intra_redundancy > th.hard_intra_redundancy):
        triggers.append(TriggerReason.INTRA_REDUNDANCY)
    if lit.chars < th.hard_min_chars:
        triggers.append(TriggerReason.TOO_SHORT)
    # ★ learn 专属触发 —— **不复用** story 的项（复用会让触发名说谎）
    if m.bare_term_hits >= th.hard_bare_term_hits:
        triggers.append(TriggerReason.BARE_TERM)
    rate = m.substitution_rate
    if rate is not None and rate < th.target_substitution_rate:
        triggers.append(TriggerReason.SUBSTITUTION_LOW)
    density = knowledge_density(m.points_mentioned, lit.chars)
    if density is not None and density < th.hard_points_per_1k:
        triggers.append(TriggerReason.LOW_DENSITY)
    expl = m.explained_ratio
    if expl is not None and expl < th.hard_explained_ratio:
        triggers.append(TriggerReason.EXPLANATION_SHALLOW)
    rev = m.review_injection_rate
    if rev is not None and rev < th.hard_review_injection:
        triggers.append(TriggerReason.REVIEW_MISSED)
    if score < th.score_threshold and TriggerReason.LOW_SCORE not in triggers:
        triggers.append(TriggerReason.LOW_SCORE)

    return Scorecard(
        global_no=global_no,
        literal=lit,
        structural=None,          # learn 无细纲结构层（story 专属）
        dimensions=dims,
        triggers=triggers,
        score=score,
        needs_review=bool(triggers),
        forced_review=False,      # learn 无「黄金三章」
    )


#: learn 的访问器表（与 story 的 `ACCESSORS` 分开 —— 输入类型不同）。
LEARN_ACCESSORS: dict[str, object] = {
    "knowledge_density": lambda m, lit: knowledge_density(
        m.points_mentioned, lit.chars
    ),
    "substitution_rate": lambda m, lit: m.substitution_rate,
    "explained_ratio": lambda m, lit: m.explained_ratio,
    "progression_rate": lambda m, lit: m.progression_rate,
    "review_injection_rate": lambda m, lit: m.review_injection_rate,
    "intra_redundancy": lambda m, lit: lit.intra_redundancy,
    "cross_chapter_overlap": lambda m, lit: lit.cross_chapter_overlap,
    "chars": lambda m, lit: float(lit.chars),
    "dialogue_ratio": lambda m, lit: lit.dialogue_ratio,
    "simile_count": lambda m, lit: float(lit.simile_count),
    "no_time_skip_ending": lambda m, lit: m.no_time_skip_ending,
}


__all__ = [
    "LEARN_ACCESSORS",
    "LEARN_DIMENSIONS",
    "LEARN_TRIGGERS",
    "LearnMetrics",
    "LearnThresholds",
    "count_bare_terms",
    "count_term_usage",
    "evaluate_learn",
    "knowledge_density",
]
