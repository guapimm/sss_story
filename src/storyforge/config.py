"""配置系统 —— 单一入口 + 强类型 + **运行时注入**。

## 为什么不照抄旧项目

旧项目有 26 个 JSON 配置文件 + 81 个环境变量，且 7 个模块各自实现
`_raw()/reload()/save()` 缓存；更严重的是 `config.py` 在**类定义期**就冻结了 settings，
导致 **121/123 个测试文件**必须在模块级修改 `os.environ` 才能隔离 —— 单进程 pytest
因此产生大量假失败（见 doc/research/00-evidence-baseline.md §14）。

本模块的设计约束：
  1. **单一来源**：所有配置从 `Settings` 读，禁止散落 `os.environ` 直读。
  2. **运行时注入**：`Settings` 是普通 Pydantic 模型，通过 `get_settings()` 取，
     可在测试中 `configure(settings)` 替换；**不在 import 期冻结**。
  3. **无静默覆盖**：不允许多个来源竞争同一字段（旧项目 `.env` 写模型被
     `llm_providers.json` 静默覆盖，见 doc/06 R-… / L-06）。
  4. **类型校验失败必须报错**：类型错误抛 ValidationError，不降级成默认值。
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #

#: 仓库根目录（src/storyforge/config.py -> 上溯三级）。
#:
#: ★ 仅作**开发期辅助**（`arch-check` 的 cwd、`.env` 提示等），**不得**用作资源根：
#: wheel 安装后本模块位于 `site-packages/storyforge/`，`parents[2]` 会解析到
#: `<venv>\Lib` —— PG-0 实测 B-2（`REPO_ROOT/config` 与 `REPO_ROOT/data` 双双不存在）。
#: 资源定位一律走 `resolve_config_dir()` / `resolve_data_dir()`。
REPO_ROOT = Path(__file__).resolve().parents[2]


#: 包内资源目录（本模块所在包 = `storyforge/` 的下一级 `_resources/config`）。
#:
#: ★ 锚点是 `Path(__file__).parent`（= 包目录本身）：源码树为
#: `src/storyforge/_resources/config`（不存在 → 回落仓库根），
#: 安装态为 `site-packages/storyforge/_resources/config`（force-include 打入，命中）。
#: ⚠️ 首版曾错写成 `parents[1]` —— 那是包的**上級目录**（src / site-packages），
#: 隔离安装验收当场抓出（config_dir 落回 `<venv>\Lib\config` 旧病灶）。
#: 提成模块常量正是为了让这个 off-by-one 能被单测钉死。
_PACKAGE_CONFIG_DIR = Path(__file__).resolve().parent / "_resources" / "config"


def resolve_config_dir(
    env_value: str | None = None,
    package_dir: Path | None = None,
    repo_dir: Path | None = None,
) -> Path:
    r"""config 目录解析（每次调用现算，**不做 import 期冻结**）。

    ## 解析顺序（高 → 低）

      1. `STORYFORGE_CONFIG_DIR` 环境变量（空串/纯空白视为未设置）
      2. 包内 `storyforge/_resources/config` —— 安装态命中：
         `pyproject.toml` 的 `force-include` 把仓库根 `config/` 打进 wheel
         （doc/11 方案甲，决策已冻结）；源码树中该目录**不存在**，自动跳过
      3. `<REPO_ROOT>/config` —— 源码树命中（开发工作流零变化）

    ## 为什么必须是函数而不是模块常量（doc/11 §2.4 陷阱四）

    `DEFAULT_CONFIG_DIR = REPO_ROOT / "config"` 在 **import 期**求值：
    安装态下它被冻结成 `<venv>\Lib\config`（不存在），
    运行期改 `STORYFORGE_CONFIG_DIR` 也无法挽救读不到的 providers.toml。
    因此本函数在**每次调用时**重新解析；`Settings` 的 `config_dir`
    字段用 `default_factory` 挂接，同样按需求值。

    ## 可注入参数（仅测试使用）

    生产调用一律无参；测试通过 `env_value` / `package_dir` / `repo_dir`
    模拟"安装态有两个候选"等场景，不必伪造 site-packages。
    """
    env = env_value if env_value is not None else os.environ.get("STORYFORGE_CONFIG_DIR")
    if env and env.strip():
        return Path(env)
    pkg = package_dir if package_dir is not None else _PACKAGE_CONFIG_DIR
    if pkg.exists():
        return pkg
    return repo_dir if repo_dir is not None else REPO_ROOT / "config"


def resolve_data_dir() -> Path:
    """data 目录解析（函数形式，去 import 期冻结）。

    当前仍解析为 `<REPO_ROOT>/data`（源码树语义，开发机 `data/` 不动）。
    ★ PG-1B 将在此加入"仅安装态切 `%APPDATA%`"分支（doc/11 §4.2 硬约束：
    无条件切换会移动开发机数据并破坏既有测试的默认路径假设）——本阶段**不动**。
    """
    return REPO_ROOT / "data"


# --------------------------------------------------------------------------- #
# 分节配置
# --------------------------------------------------------------------------- #


class LLMSettings(BaseModel):
    """LLM 调用层配置。

    注意：模型名**不在这里** —— 模型与能力声明在 config/providers.toml，
    节点按「能力需求」选模型（决策 D-09）。此处只放调用行为参数。
    """

    #: 单次调用超时（秒）。
    #:
    #: ★ 必须与 `ClientConfig.max_output_tokens_cap` **相容**，否则配置自相矛盾。
    #:
    #: 实测（2026-09-26 真实 S0，MiMo v2.6-flash）：吞吐 **13.7 tok/s**。
    #: 旧值 300s 只够 ~4,110 token（≈5,870 中文字），而输出上限 `cap=32768`
    #: 需要 **2,392s** —— 也就是说 `cap` 在旧超时下**物理不可达**，
    #: 长章/细纲批次会以"超时"收场，把配置缺陷伪装成"模型慢"。
    #:
    #: 取 2,400s 的理由：正好覆盖 `cap=32768` 在实测吞吐下跑满所需的时间，
    #: 使"输出上限"与"超时上限"**互不成为对方的隐形天花板**。
    #: 代价是单次调用最长可挂 40 分钟 —— 但真实失败大多来自 provider 拒绝
    #: 或空输出，那些会立刻返回，不会真的等满。
    default_timeout_s: float = 2400.0
    max_attempts: int = 3

    #: 流式空闲超时下限/上限（旧项目实测：书17/18 卡死根因是缺少循环内静默检查）
    stream_idle_min_s: float = 20.0
    stream_idle_max_s: float = 60.0

    #: 单次调用的输出预算下限，保证正文不被思考挤没
    min_output_tokens: int = 1024


class MemorySettings(BaseModel):
    """记忆预算 —— **随篇幅增长**（对策⑨）。

    旧项目恒定 12,000 字，导致记忆密度从第 50 章的 4.8% 衰减到第 1500 章的 0.16%，
    模型只能把当下场景反复描摹到字数达标 —— 这是「啰嗦 + AI 味」的最底层机械成因。
    """

    base_budget_chars: int = 12_000
    #: 对数增长系数；budget = base * (1 + factor * log2(chapter_no/50 + 1))
    growth_factor: float = 0.5
    #: 各块占比（总和应为 1.0）
    share_timeline: float = 0.20
    share_states: float = 0.25
    share_rag: float = 0.15
    share_summaries: float = 0.25
    share_other: float = 0.15

    @field_validator("share_other")
    @classmethod
    def _shares_sum_to_one(cls, v: float, info) -> float:
        d = info.data
        total = sum(
            d.get(k, 0.0)
            for k in ("share_timeline", "share_states", "share_rag", "share_summaries")
        ) + v
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"记忆预算占比之和必须为 1.0，当前为 {total:.4f}")
        return v


class ScorecardSettings(BaseModel):
    """质量打分卡阈值（P2 已按旧项目真实分布标定）。

    口径继承旧项目已用书 23 的 47 章 baseline 反解并对齐的算法，
    **但阈值重新标定**：旧 `dialogue_floor = 0.12` 明显过松
    （Editor 反复批评"对话占比极低"而 12% 就合格；旧基线 p50 = 0.106）。

    ## 双层设计（关键）

    单层阈值实测会让 **47/47 章全部触发**（书 23 的 dialogue p95 才 0.292）
    → 闸门退化成"全部送审"，等于没有闸门。

      · `hard_*`   —— 明确缺陷，任一命中即送 LLM 审校（少而准）
      · `target_*` —— 目标水位，只影响总分（逐期抬升）

    标定依据见 `config/settings.toml` 的注释与
    `tests/golden/test_baseline_reproduction.py`。
    """

    # ── hard 层（明确缺陷）──
    hard_cross_overlap: float = 0.15
    hard_intra_redundancy: float = 0.15
    hard_dialogue_floor: float = 0.05
    hard_cliche_hits: int = 8
    hard_simile_count: int = 6
    hard_strong_emotion: int = 4
    hard_sentence_cv_floor: float = 0.30
    hard_min_chars: int = 2500
    hard_outline_adherence: float = 0.5

    # ── target 层（参与总分）──
    target_dialogue_ratio: float = 0.25
    target_cross_overlap: float = 0.06
    target_intra_redundancy: float = 0.08
    target_cliche_hits: int = 2
    target_simile_count: int = 3
    target_strong_emotion: int = 2
    target_sentence_cv: float = 0.50
    target_min_chars: int = 3500
    target_outline_adherence: float = 0.8

    # ── 总分与无条件送审 ──
    score_threshold: float = 0.62
    #: 黄金三章无条件 LLM 审（D-14 C-5）
    always_review_chapters: tuple[int, ...] = (1, 2, 3)
    #: 同一章重写次数上限，超过触发人工确认 C-7
    max_rewrites: int = 3

    # ── 口径（不得随意改：改了 baseline 即失效）──
    #: 跨章重叠的回看窗口（章）
    cross_window: int = 5
    #: n-gram 长度
    ngram: int = 4


class WriterSettings(BaseModel):
    """正文写作（P4-3 长度闭环）。

    ## 为什么上限**不自动重写**

    旧项目实测"重写空转"（`no_improve:6->6`）—— 重写一次的成本等于再写一章，
    而超长本身是可控缺陷（由打分卡记录并送审）。截断更糟：会切断句子
    并丢掉章末钩子，而钩子是下一章的入口。

    因此本节的语义是：
      · 低于 `min_chars` → **续写一次**（只发尾部指令，省钱且不丢已写内容）
      · 高于 `soft_max`   → **只记录**，交打分卡与审校判定
    """

    #: 目标字数下限。标定依据：旧项目 47 章 p50 = 5065、p5 = 4149。
    #: 定在 p5 以上，即"至少不低于旧项目的偏短章"。
    min_chars: int = 4000
    #: 硬性上限（超过视为注水）。旧项目 p95 = 6520。
    soft_max: int = 7000
    #: 目标下限不足时最多续写几次
    max_continue: int = 1


class StructureGateSettings(BaseModel):
    """卷级扩张度闸门（对策②）。

    ⚠️ **必须 enabled** —— 旧项目 `ultra_structure_config.json` 的指标设计正确，
    但为 `enabled: false, shadow: true`，导致 400 章全挤第 1 卷、412 项 lint error 全部放行。

    ## 卷规模为什么是**相对区间**而不是绝对章数（实测修正）

    初版用绝对区间 `[150, 250]`，配合「各卷之和 = 目标章数」，
    会**把书锁死为固定卷数**：

        目标 400 章 → 每卷 150~250 → 只可能 2 卷（200+200）
        目标 800 章 → 只可能 4 卷（200×4）

    实测：模型规划 4 卷 × 100 章（结构上完全合理）时被拒，
    而它**无法知道**唯一合法解是"正好 2 卷" —— 这是**过约束**：
    闸门在替作者定卷数，而不是在检查质量。

    更根本的是：旧项目的真实缺陷不是"每卷章数少"，而是**分配不均**
    （400 章全挤第 1 卷）。那一点由独立的 `max_volume_share` 检查覆盖。

    因此改为相对占比 + 绝对下限：
      · `volume_share_min/max` —— 每卷占全书的比例区间
      · `chapters_per_volume_floor` —— 每卷章数绝对下限（防"每卷 3 章"碎片化）
    """

    enabled: bool = True
    new_locations_per_volume_min: int = 2
    new_factions_per_volume_min: int = 2
    milestone_max_gap: int = 25
    location_reuse_ratio_max: float = 0.5
    #: 每卷占全书章数的比例区间（相对；不锁死卷数）
    volume_share_min: float = 0.15
    volume_share_max: float = 0.60
    #: 每卷章数绝对下限（防碎片化：低于此值不是"卷"，是"章组"）
    chapters_per_volume_floor: int = 20
    repair_attempts: int = 5


class PipelineSettings(BaseModel):
    """三级流水线与协调器（决策 D-13）。"""

    #: 骨架批次大小（章）
    skeleton_batch_size: int = 50
    #: 细纲批次大小（章）。
    #:
    #: ## ★ 为什么是 5 而不是 15（实测 + 成本模型，不是拍脑袋）
    #:
    #: 原值 15 在真实链路上**连续 3 次超时**（每次 300s，共 1135s 全废）。
    #:
    #: 实测单批成本（book 16 / MiMo v2.5 / `reasoning_effort=high`）：
    #:
    #:     1 章 →  48.2s      3 章 → 115.8s      6 章 → 147.9s
    #:
    #: 线性拟合：`耗时 ≈ 39.9s + 19.2s × 章数`
    #:   → **15 章预测 328s > 300s 超时上限**，与实测的三连败完全吻合。
    #:
    #: 取 5 章：预测 136s，占超时上限 **45%**，余量充足。
    #:
    #: ## 为什么"批次小"本身也是对的（不只是为了不超时）
    #:
    #: 超时/lint 失败 = **整批作废**（失败批次不落盘）。批越大，
    #: 一次失败白烧的 token 越多（15 章失败 = 白烧 15 章的 token）。
    #: 而批数变多可由**并发**吸收 —— D-13 的流水线本就是并行的，
    #: 3 批 × 5 章的总墙钟 ≈ 1 批的时间。
    #:
    #: ⚠️ 约束关系比具体数字重要：`批次 × 每章耗时 + 固定开销` 必须
    #: **显著小于**超时上限。换模型或换 effort 后需按同样方法重测
    #: （脚本 `scripts/diag_batch_model.py`）。
    detail_batch_size: int = 5
    #: 正文最多领先细纲的章数（防跑偏导致后续细纲全废）
    chapter_lead_limit: int = 3
    #: 闸门等待轮询间隔（秒）—— 等待必须留痕，不空转
    gate_poll_interval_s: float = 2.0
    #: 闸门最长等待（秒），超时则告警而非静默死等
    gate_timeout_s: float = 3600.0


class ObservabilitySettings(BaseModel):
    """观测配置（对策⑦）。"""

    #: 是否把每次 LLM 调用写入 llm_calls 表
    persist_calls: bool = True
    #: 遥测保留天数（旧项目 4 张遥测表占全库 54% 且**无 TTL**）
    telemetry_ttl_days: int = 90
    #: 空输出率告警阈值（旧项目实测 22.2%）
    blank_rate_alert: float = 0.03


class ResearchSettings(BaseModel):
    """联网研究配置（learn 专属，阶段 2-2）。

    ## 触发策略（用户决策：按知识点数量目标决定轮次）

    `learn` 的目标流程要求"按篇幅需要**动态调整**，一次不够后续动态补充"。
    判据不是"搜了几轮"，而是**知识点够不够**：

      1. 按篇幅档位算目标知识点数（`points_per_scale`）
      2. 每轮研究产出若干知识点，累计到目标就停
      3. 不够则继续搜（换角度），直到 `max_rounds` 或目标达成

    这样"停"和"续"都有可解释的依据，而不是拍一个轮次数。
    """

    #: Tavily API key 的环境变量名（值本身从 `.env` 读，不入库）
    api_key_env: str = "TAVILY_API_KEY"
    #: 检索服务地址
    endpoint: str = "https://api.tavily.com/search"

    #: 单轮检索条数
    max_results_per_query: int = 5
    #: 检索深度：`basic`（快、便宜）/ `advanced`（深、贵）
    search_depth: str = "basic"

    #: ★ 各篇幅档位的**目标知识点数** —— 决定研究轮次
    #:
    #: 依据：旧项目 `estimator.py` 用 `points_count` 推导章数
    #: （`chapter_count = max(mini, min(cap, points_count * 4))`），
    #: 即**约 4 章讲 1 个知识点**。此处反推目标点数，
    #: 使"篇幅 ↔ 知识点"两端一致。
    #:
    #: ★★ 上限必须 ≤ 学习地图的**容量**（`graph/nodes/learning.py:SCALE_PROFILE`
    #: 的 `模块数上限 × 每模块点数`），否则会出现"搜了一堆但地图装不下"
    #: → 资料浪费，或模型为塞进地图而删减内容。
    #:
    #: 实测修正（被 `test_scale_profile_covers_research_target` 抓到）：
    #: ultra 原定 150，但 `RESEARCH_PROFILE` 的 ultra 是
    #: **10~14 模块 × 10 点 = 容量 140** → 150 > 140 **装不下**。
    #: 现按容量上限对齐为 140（不是把容量改大 —— 那是旧项目已验证的规模）。
    points_per_scale: dict[str, int] = Field(
        default_factory=lambda: {
            "fast": 8,          # 容量 12（2~3 模块 × 4）
            "standard": 25,     # 容量 30（3~5 × 6）
            "grand": 60,        # 容量 80（6~10 × 8）
            "ultra": 140,       # 容量 140（10~14 × 10）—— 取满
        }
    )

    #: 单主题最多研究轮数（防无限搜）
    max_rounds: int = 6
    #: 检索结果缓存 TTL（天）—— 旧项目有 `RESEARCH_CACHE_TTL_DAYS`
    cache_ttl_days: int = 30
    #: 网络超时（秒）
    timeout_s: float = 30.0
    #: 失败重试次数（网络抖动不该让整本书失败）
    max_attempts: int = 3


class TeachingSettings(BaseModel):
    """教学进度调度配置（learn 专属，阶段 2-4）。

    ## 间隔表的单位是「章」不是「天」

    这是**小说**：读者的"遗忘曲线"由**阅读进度**决定，不是墙钟时间。
    旧项目 `config.py:120` 用 `REVIEW_INTERVALS="3,7,15,30"`（章）是对的，
    本项目沿用。
    """

    #: 艾宾浩斯复习间隔（章）。逐字来自旧项目 `REVIEW_INTERVALS="3,7,15,30"`。
    #:
    #: ★ 走完全部间隔 = **毕业**（状态转 `已复用`，不再挑中）。
    #: 故数组长度 = 总复习次数。
    intervals: list[int] = Field(default_factory=lambda: [3, 7, 15, 30])

    #: 顺延延迟（章）—— 本章没自然用上时推后多少章。
    #: 旧项目 `defer_reviews(..., delay=2)` 同值。
    defer_delay: int = 2

    #: 无新知识点时每章最多塞几个复习点。
    #: 旧项目 `cap = 1 if has_new_points else min(limit, 3)`。
    max_reviews_per_chapter: int = 3

    #: 不注入复习的节拍类型 —— 「高潮/Boss/情绪章不注入，避免打断场面」
    #: （旧项目 `pick_due_reviews` 的 `beat in ("boss","climax","emotion")`）
    excluded_beats: list[str] = Field(
        default_factory=lambda: ["boss", "climax", "emotion"]
    )


class Settings(BaseSettings):
    """全局配置。所有模块通过 `get_settings()` 获取，不得直读环境变量。"""

    model_config = SettingsConfigDict(
        env_prefix="STORYFORGE_",
        env_nested_delimiter="__",
        extra="forbid",  # 拼错的环境变量必须报错，不静默忽略
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """接入 `config/settings.toml` —— 此前该文件**从未被读取**。

        ★ 断点（2026-09-26 核实）：`config/settings.toml` 自称"全局配置唯一入口
        （决策 D-02）"，但 `SettingsConfigDict` **没有 `toml_file`**，
        且全仓无任何读它的代码 —— 于是改它**零效果**，
        而真正生效的是 `config.py` 的默认值。两个来源并存且互相矛盾，
        正是本项目明令禁止的"两个来源竞争同一字段"（旧项目 L-06 同型）。

        ## 优先级（早 → 高，**靠前者覆盖靠后者**）

            1. `init_settings`（显式传参，如测试的 `configure()`）
            2. `.env` / 环境变量 `STORYFORGE_*`   ← 本机覆盖，**必须高于 TOML**
            3. `config/settings.toml`            ← 可提交的基线
            4. 字段默认值（`Settings` 自身）

        ⚠️ `settings_customise_sources` 返回的元组里**越靠前优先级越高**
        （已实测：`TomlConfigSource` 放在 `env_settings` **之前**会让 TOML
        覆盖环境变量 —— 那是错的，本机覆盖必须赢）。故 TOML 排在 env **之后**。

        ★ 环境变量仍高于 TOML，因此本改动**不改变任何既有行为** ——
        已逐键核对：接入前 TOML 每个键的值都与 `config.py` 默认值相同
        （唯一真分歧 `detail_batch_size=15` 与 `default_timeout_s=300`
        是 TOML 自身过期，已一并修正为 5 / 2400，见该文件注释）。

        TOML 不存在时**静默跳过** —— 安装态可能没有它，
        而"没有配置文件"是合法状态，不是错误。
        """
        from pydantic_settings import TomlConfigSettingsSource

        toml_path = resolve_config_dir() / "settings.toml"
        if not toml_path.exists():
            return (init_settings, env_settings, dotenv_settings, file_secret_settings)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=toml_path),
        )

    app_name: str = "storyforge"
    #: ★ `default_factory` 按需解析（doc/11 §2.4）：旧写法 `= DEFAULT_CONFIG_DIR`
    #: 在 import 期冻结，安装态下指向不存在的 `<venv>\Lib\config`。
    data_dir: Path = Field(default_factory=resolve_data_dir)
    config_dir: Path = Field(default_factory=resolve_config_dir)

    #: 当前默认模式（story/learn）。
    #:
    #: ⚠️ 这只是**默认值**，不是运行时真相 —— 每本书的模式存
    #: `books.mode`（阶段 0 起 `BookRepository.upsert` 会真正写入），
    #: 由 `modes.registry.mode_for()` 翻译成能力实现。
    #: 此前该字段是"只写不读的死配置"。
    mode: Literal["story", "learn"] = "story"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    scorecard: ScorecardSettings = Field(default_factory=ScorecardSettings)
    writer: WriterSettings = Field(default_factory=WriterSettings)
    structure_gate: StructureGateSettings = Field(default_factory=StructureGateSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    teaching: TeachingSettings = Field(default_factory=TeachingSettings)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "storyforge.db"

    @property
    def checkpoint_db_path(self) -> Path:
        return self.data_dir / "checkpoints.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# .env 密钥加载（★ 唯一允许读密钥文件的入口）
# --------------------------------------------------------------------------- #


def resolve_secret(env_name: str) -> str | None:
    """按环境变量名取密钥，**空串视为未配置**（返回 None）。

    ## 为什么需要这个函数（而不是各处 `os.environ.get`）

    契约规则 12 禁止业务代码散落直读 `os.environ` ——
    旧项目 26 个 JSON + 81 个环境变量 + 7 个模块各自缓存，
    且 `.env` 写的模型被 `llm_providers.json` **静默覆盖**。

    因此所有密钥读取都必须经本模块（`config.py` 是规则 12 的例外之一）。

    ⚠️ **永不回传值到日志** —— 调用方只应用它去发请求，
    不要把返回值写进报错信息（`doctor` 打印的是**键名**，不是值）。
    """
    value = os.environ.get(env_name, "")
    return value.strip() or None


#: `.env` 的候选位置：仓库根优先，其次当前工作目录。
#: 只读**仓库根**那份即可满足绝大多数场景，留第二个是为了
#: "在子目录里跑脚本"时仍能找到。
def dotenv_paths() -> list[Path]:
    return [REPO_ROOT / ".env"]


def python_child_env() -> dict[str, str]:
    """给 **Python 子进程**的环境：确保其 stdio 按 UTF-8 编解码。

    ## 为什么存在（2026-09-26 PG-1A 期间实测炸出）

    Windows 控制台默认 GBK。管道模式下，Python 子进程按自己的
    `PYTHONIOENCODING` 编码输出，父进程 `subprocess.run(text=True)`
    却按系统区域解码 —— 项目规约要求"跑命令前设 `PYTHONIOENCODING=utf-8`"，
    于是子进程写 UTF-8、父进程按 GBK 读，输出里一有中文就
    `UnicodeDecodeError`（`arch-check` 实测在 0xa3 处踩中）。

    本函数把父子两侧**都钉死 UTF-8**，与调用方控制台无关。
    本模块是契约规则 12 允许直读 `os.environ` 的唯一例外处，
    因此该构造放在这里而不是各调用点散落复制。
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def load_dotenv(*, override: bool = False) -> list[str]:
    """把 `.env` 里的键值注入 `os.environ`，返回**被注入的键名**（不是值）。

    ## 设计约束

    1. **不覆盖已存在的环境变量**（`override=False` 为默认）。
       真实 shell 环境优先于文件 —— 这样 CI、临时 `$env:X=...`、
       以及"临时换一个 key 试试"都能生效，而不会被文件悄悄盖掉。
       旧项目的教训正是**静默覆盖**：`.env` 写的模型被 `llm_providers.json`
       覆盖，且没有任何提示。

    2. **只回传键名，永不回传值** —— 调用方可以放心打印。
       密钥绝不能进日志（`doctor` 会打印"未配置: [...]"这类信息，
       那是键名，不是值）。

    3. **格式宽容但解析严格**：支持 `KEY=VALUE`、`export KEY=VALUE`、
       `#` 注释、空行、值两侧的引号。解析不出 `=` 的行**跳过**而不是报错 ——
       `.env` 常常被手工编辑，因为一行笔误就让整个程序起不来是不合理的。

    4. **文件不存在不是错误**（返回空列表）。CI 里通常没有 `.env`。

    ⚠️ 本函数**只被 CLI 入口与测试显式调用**，不在 import 期自动执行 ——
    保持"配置在运行时注入"这一设计（见模块 docstring）。
    """
    injected: list[str] = []
    for path in dotenv_paths():
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.lower().startswith("export "):
                s = s[7:].lstrip()
            key, sep, value = s.partition("=")
            if not sep:
                continue
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            # 去掉成对引号（值里含 # 或空格时必须写引号）
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if not override and key in os.environ:
                continue
            os.environ[key] = value
            injected.append(key)
    return injected


# --------------------------------------------------------------------------- #
# 运行时注入（关键设计：不在 import 期冻结）
# --------------------------------------------------------------------------- #

_lock = threading.RLock()
_current: Settings | None = None


def get_settings() -> Settings:
    """取当前配置。首次调用时构建默认配置。"""
    global _current
    with _lock:
        if _current is None:
            _current = Settings()
        return _current


def configure(settings: Settings | None = None, **overrides) -> Settings:
    """运行时替换配置 —— 供测试与 CLI 使用。

    这是与旧项目的关键差异：旧项目在**类定义期**冻结 settings，
    导致测试必须在 import 前改 `os.environ`（121/123 个测试文件这么做），
    单进程 pytest 因此产生假失败。

    覆盖会**经过 Pydantic 校验**并合并到当前配置上（含嵌套分节），
    因此 `configure(memory={"base_budget_chars": 999})` 会正确构造
    `MemorySettings`，而不是把 dict 直接塞进去。
    """
    global _current
    with _lock:
        if settings is not None:
            _current = settings
            return _current

        base = _current or Settings()
        if not overrides:
            _current = Settings()
            return _current

        # 深度合并：把 overrides 合并进当前配置的 dict，再整体重新校验。
        # 不用 model_copy(update=...) —— 它不做嵌套校验，会把 dict 直接塞进字段。
        merged = base.model_dump()
        for key, value in overrides.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        _current = Settings.model_validate(merged)
        return _current


def reset_settings() -> None:
    """清空当前配置（测试 teardown 用）。"""
    global _current
    with _lock:
        _current = None


__all__ = [
    "REPO_ROOT",
    "resolve_config_dir",
    "resolve_data_dir",
    "python_child_env",
    "dotenv_paths",
    "load_dotenv",
    "LLMSettings",
    "MemorySettings",
    "ScorecardSettings",
    "StructureGateSettings",
    "PipelineSettings",
    "ObservabilitySettings",
    "Settings",
    "ValidationError",
    "get_settings",
    "configure",
    "reset_settings",
]
