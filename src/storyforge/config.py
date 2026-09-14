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

import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #

#: 仓库根目录（src/storyforge/config.py -> 上溯三级）
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_DATA_DIR = REPO_ROOT / "data"


# --------------------------------------------------------------------------- #
# 分节配置
# --------------------------------------------------------------------------- #


class LLMSettings(BaseModel):
    """LLM 调用层配置。

    注意：模型名**不在这里** —— 模型与能力声明在 config/providers.toml，
    节点按「能力需求」选模型（决策 D-09）。此处只放调用行为参数。
    """

    default_timeout_s: float = 300.0
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
    """

    enabled: bool = True
    new_locations_per_volume_min: int = 2
    new_factions_per_volume_min: int = 2
    milestone_max_gap: int = 25
    location_reuse_ratio_max: float = 0.5
    chapters_per_volume_min: int = 150
    chapters_per_volume_max: int = 250
    repair_attempts: int = 5


class PipelineSettings(BaseModel):
    """三级流水线与协调器（决策 D-13）。"""

    #: 骨架批次大小（章）
    skeleton_batch_size: int = 50
    #: 细纲批次大小（章）
    detail_batch_size: int = 15
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


class Settings(BaseSettings):
    """全局配置。所有模块通过 `get_settings()` 获取，不得直读环境变量。"""

    model_config = SettingsConfigDict(
        env_prefix="STORYFORGE_",
        env_nested_delimiter="__",
        extra="forbid",  # 拼错的环境变量必须报错，不静默忽略
    )

    app_name: str = "storyforge"
    data_dir: Path = DEFAULT_DATA_DIR
    config_dir: Path = DEFAULT_CONFIG_DIR

    #: 一期只支持 story（决策 D-08）；learn 保留接口但不实现
    mode: Literal["story", "learn"] = "story"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    scorecard: ScorecardSettings = Field(default_factory=ScorecardSettings)
    writer: WriterSettings = Field(default_factory=WriterSettings)
    structure_gate: StructureGateSettings = Field(default_factory=StructureGateSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

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
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_DATA_DIR",
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
