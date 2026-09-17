"""learn 模式 —— 一期**显式占位**（决策 D-08）。

## ★ 正向着色：为什么是 raise 而不是空实现

旧项目的方向是**反的**：story 侧给 learn 的钩子写空实现
（`story_flow.py:171-201` 有 **8 个 `@staticmethod` 空实现**）。

| | 反向空实现（旧） | 正向着色（本设计） |
|---|---|---|
| 代码位置 | story 侧给 learn 写空 | learn 侧为自己的能力占位 |
| 调用结果 | 返回 `""` / `[]` —— **静默出错** | `raise NotImplementedError` |
| 调用方能否区分 | ❌ "没做" 与 "做了但为空" 无法区分 | ✅ 第一次调用就暴露 |
| 加 learn 时 | 要**删掉** story 里的 8 个空实现 | 只需**实现**本文件的 Protocol |

本项目已实测过一次"空实现静默返回错误结果"的代价：
`CONTEXT_SOURCES` 封闭元组 + 静默降级 → learn 首次上报归因即崩。
**能报错就不要沉默。**

## 一期范围

只提供 story 需要的 3 个能力（`prompts` / `genre` / `estimator`）。
learn 专属的 5 个（`learning_map` / `ledger` / `cards` / `research` /
`verifier`）**不提供属性** —— 于是 `require_capability(mode, "ledger")`
会抛 `CapabilityNotAvailable`，带上"哪个能力、哪个模式、怎么办"。

## 阶段 2 要做什么（本文件届时被替换）

    ① 联网研究    ResearchProvider  → 旧项目 `search/provider.py`（Tavily）
    ② 学习地图    LearningMapProvider → 从研究结果抽知识点
    ③ 平替修辞    由研究结果 + 流派生成（需用户确认，新增确认点）
    ④ 知识卡      KnowledgeCardProvider → 旧项目 `post_processor.py`
    ⑤ 教学账本    TeachingLedgerProvider → 艾宾浩斯间隔复习
    ⑥ learn 打分卡 —— 维度集合与 story **不同**（实测：
       标准说明文在 story 卡上得 0.51 分且 `dialogue_low` 硬触发，
       而"教学章不要对话"是**正确**写法 —— 是维度错配，不是调参问题）
"""

from __future__ import annotations

from storyforge.modes.protocols import CapabilityNotAvailable


def _not_implemented(capability: str, how: str) -> CapabilityNotAvailable:
    """构造带**实现指引**的异常（不是一句干巴巴的 raise）。"""
    return CapabilityNotAvailable(
        f"learn 模式的能力 {capability!r} 尚未实现（决策 D-08：一期只做 story）。\n"
        f"实现方式：{how}\n"
        f"契约：见 src/storyforge/modes/protocols.py 的对应 Protocol。"
    )


# --------------------------------------------------------------------------- #
# learn 专属的 5 个 Provider —— 显式 raise
# --------------------------------------------------------------------------- #


class _UnimplementedLearningMap:
    """`LearningMapProvider` 占位（learn 专属）。"""

    def build_map(self, topic: str):  # noqa: ANN201
        raise _not_implemented(
            "learning_map",
            "实现 LearningMapProvider.build_map()：联网研究 → 抽知识点 → "
            "产出 LearningMap（旧项目参考 `app/agents/researcher.py`）。",
        )


class _UnimplementedLedger:
    """`TeachingLedgerProvider` 占位（learn 专属）。"""

    def record_taught(self, chapter_no: int, points: list) -> None:
        raise _not_implemented(
            "ledger",
            "实现 TeachingLedgerProvider：记录已教知识点 + 按艾宾浩斯间隔"
            "排下次复习章（旧项目有 review_count/next_review_ch 字段但**无调度实现**）。",
        )

    def untaught(self) -> list:
        raise _not_implemented(
            "ledger", "实现 TeachingLedgerProvider.untaught()。"
        )


class _UnimplementedCards:
    """`KnowledgeCardProvider` 占位（learn 专属）。"""

    def extract_cards(self, prose: str, points: list) -> list:
        raise _not_implemented(
            "cards",
            "实现 KnowledgeCardProvider.extract_cards()：从正文抽章末知识卡"
            "（旧项目参考 `app/agents/post_processor.py:214`）。",
        )

    def render_section(self, cards: list) -> str:
        raise _not_implemented(
            "cards",
            "实现 KnowledgeCardProvider.render_section()：渲染【章末知识卡】小节"
            "（旧项目 `flowmode/base.py:207-210`）。",
        )


class _UnimplementedResearch:
    """`ResearchProvider` 占位（learn 专属）。"""

    def search(self, query: str, max_results: int = 5) -> list:
        raise _not_implemented(
            "research",
            "实现 ResearchProvider.search()：接检索服务"
            "（旧项目 `app/search/provider.py` 的 TavilyProvider）。",
        )

    def research(self, topic: str):  # noqa: ANN201
        raise _not_implemented(
            "research",
            "实现 ResearchProvider.research()：多轮检索 + 分阶段断点续跑"
            "（旧项目 `research_store.py` 的 {queries/snippets/map/verdicts} 四阶段）。",
        )


class _UnimplementedVerifier:
    """`FactVerifier` 占位（learn 专属）。"""

    def verify(self, claims: list) -> list:
        raise _not_implemented(
            "verifier",
            "实现 FactVerifier.verify()：核验教学内容的正确性。"
            "★ `supported=None` 表示**无法判定**，不得当 False（缺失不得当 0）。",
        )


# --------------------------------------------------------------------------- #
# learn 的 PromptPack / GenrePlugin / Estimator
# --------------------------------------------------------------------------- #


class LearnPromptPack:
    """learn 的提示词整包（占位）。

    ★ `dir_name = "learn"` 指出了正确方向：learn 的模板应放
    `infra/prompts/learn/writer_system.md`，从而与 story 的扁平目录
    **不撞名** —— 这是 `PROMPTS_DIR` "单一扁平目录、无模式维度"的解法
    （learn 的 P1 单点前提）。

    三个方法一期都 raise：**返回空串会静默沿用 story 的写作规范**
    （"不要对话"与"对话占比 ≥25%"直接冲突），比报错危险得多。
    """

    dir_name = "learn"

    def writer_system_extension(self) -> str:
        raise _not_implemented(
            "prompts",
            "写 `infra/prompts/learn/writer_system.md`：教学小说的写作规范"
            "（★ 与 story 冲突的维度必须重写：删掉「对话占比」与「比喻过多」——"
            "教学章的比喻是**教学手段**而非 AI 味）。",
        )

    def extractor_instructions(self) -> str:
        raise _not_implemented(
            "prompts",
            "写 learn 的抽取指令：除实体/状态外还要抽**知识点**与**知识卡**。",
        )

    def critic_instructions(self) -> str:
        raise _not_implemented(
            "prompts",
            "写 learn 的审校指令（重写全部 6 项审查重点）。",
        )


class LearnEstimator:
    """learn 的成本预估（占位）。

    ★ 公式差异（旧项目 `estimator.py:30-31` 实测）：
      · **每章 research 1 次**（story 恒 0）
      · 产知识卡（writer 每章多 700 输出 token）
      · 章数由 `points_count` 推导（`max(mini, min(cap, points*4))`）
    三者都不是"改个系数"，故必须独立实现而非参数化 story 的。
    """

    def estimate(self, scope, scale: str, quality: str = "balanced"):  # noqa: ANN201
        raise _not_implemented(
            "estimator",
            "实现 LearnEstimator.estimate()：含 research 调用（每章 1 次）与"
            " knowledge cards 输出（+700 tok/章），章数按 points_count 推导。",
        )


class LearnMode:
    """learn 模式对象。

    ## ★ 只提供 story 也有的 3 个能力，且都是占位实现

    `learning_map` / `ledger` / `cards` / `research` / `verifier`
    **不在此类上定义属性** —— 于是

        require_capability(LearnMode(), "ledger")
        → CapabilityNotAvailable: 能力 'ledger' 未被模式 'learn' 提供（learn 专属）…

    这是刻意的：若在此处也放占位属性，`require_capability` 会拿到对象后
    在**调用时**才炸；不放属性则**取的时候**就炸，更早暴露。
    """

    name = "learn"

    def __init__(self) -> None:
        self.prompts = LearnPromptPack()
        self.estimator = LearnEstimator()
        #: 流派插件：learn 用**修仙流派的 25 张卡**（阶段 2 移植
        #: `flows.py` 到 `config/flows/xianxia.toml`），一期先留空占位。
        self.genre = _UnimplementedGenre()


class _UnimplementedGenre:
    """learn 的流派插件占位。

    ★ learn 的"流派"与 story 的"题材"是**两个轴**：
      · story：题材（18 个，`config/genres/*.toml`）
      · learn：题材（同上）+ **修仙流派**（25 个，如「悟道流」）
    用户明确要求"只玄幻修仙 + 流派可多选"，故 learn 的流派数据源是
    `flows.py` 的 25 张卡（含 75 条叙事装置 + 25 条示例弧线）。
    """

    def writer_blocks(self, ctx) -> list[str]:  # noqa: ANN001
        raise _not_implemented(
            "genre",
            "移植旧项目 `app/flows.py` 的 25 个修仙流派到 "
            "`config/flows/xianxia.toml`，再由 learn 的 GenrePlugin 读取"
            "（每卡含 name/scenes/hook/structure/protagonist/narrative_devices/arc_example）。",
        )

    def extract_hints(self) -> str:
        raise _not_implemented(
            "genre", "实现 learn 的流派 extract_hints()（同上，依赖 25 张卡）。"
        )


def make_learn_mode() -> LearnMode:
    """构造 learn 模式（一期：可构造，但调用能力即抛错）。"""
    return LearnMode()


__all__ = [
    "LearnMode",
    "LearnPromptPack",
    "LearnEstimator",
    "make_learn_mode",
]
