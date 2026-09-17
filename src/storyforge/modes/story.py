"""story 模式 —— 一期唯一实现（决策 D-08）。

## story 只提供 3 个能力

    提示词包   PromptPack    → StoryPromptPack
    流派插件   GenrePlugin   → StoryGenrePlugin
    成本预估   Estimator     → StoryEstimator

learn 专属的 5 个（`learning_map` / `ledger` / `cards` / `research` /
`verifier`）**story 侧不实现、也不占位** —— 调用方用
`require_capability()` 取，缺失时得到带修复指引的错。
这与旧项目"story 侧给 learn 钩子写 8 个 `@staticmethod` 空实现"
（`story_flow.py:171-201`）方向相反。

## ★ story 的公式差异（相对 learn，旧项目 `estimator.py:30-31` 实测）

    · 无知识点（不计 points_count）
    · **skip 联网研究**（省 1 次调用 + 全部网络依赖）
    · 不产知识卡（writer 每章少 700 输出 token）
    · 架构师调用算 3 次输出量（含「故事导演」环节）

## 为什么 Estimator 不查数据库

旧项目的 `_avg_speed()` 裸查 `metrics` 表取历史速度，
缺表直接抛 `OperationalError` → **整个预估接口 500**
（而预估是创建书的前置步骤）。本实现**只用档位默认速度**，
把"历史速度"留作后续可注入项 —— 预估接口不该依赖遥测表的存在。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from storyforge.infra.prompts.library import _load
from storyforge.modes.genres import GenreCard, load_genre
from storyforge.modes.protocols import Estimate, GenreContext, Scope
from storyforge.modes.scales import scale_spec

#: 每类调用的预期输出 token（逐条抄录旧项目 `estimator.OUT_TOKENS`）
OUT_TOKENS: dict[str, int] = {
    "research": 3500,
    "architect": 4500,
    "worldbuilder": 3000,
    "write": 3200,
    "cards": 700,
    "memory": 600,
    "review": 900,
}

#: 每章固定 LLM 调用次数（write + 合并后处理 + 抽查 review×0.5）
CALLS_PER_CHAPTER = 2.5

#: 每章输入 token 估计（圣经 + 记忆 + 大纲注入）
PER_CHAPTER_INPUT_TOKENS = 5500

#: 档位默认输出速度（tok/s）—— 旧项目 `_avg_speed` 的降级值
DEFAULT_SPEED: dict[str, float] = {"fast": 45.0, "balanced": 22.0, "premium": 35.0}


# --------------------------------------------------------------------------- #
# PromptPack
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StoryPromptPack:
    """story 的提示词整包。

    `dir_name = ""` 是刻意的：story 的模板就在
    `infra/prompts/` 扁平目录下（`writer_system.md` 等），
    保持既有 3 个调用点**零改动**。learn 会用自己的子目录名
    （如 `"learn"`），从而不与 story 撞名 —— 这正是
    `PROMPTS_DIR` 此前"单一扁平目录、无模式维度"的解法。
    """

    dir_name: str = ""

    def writer_system_extension(self) -> str:
        """追加到 writer 的 system prompt。

        story 的写作约束已全部在 `writer_system.md` 里，
        故这里返回空串（不是"忘了实现"——见 `modes/learn.py` 的对照）。
        """
        return ""

    def extractor_instructions(self) -> str:
        """抽取节点的额外指令（story 无额外要求）。"""
        return ""

    def critic_instructions(self) -> str:
        """审校节点的额外指令（story 无额外要求）。"""
        return ""

    def writer_system(self) -> str:
        """story 的 writer system prompt 正文（从既有模板读）。

        `pack=self.dir_name`（`""`）→ 读 `prompts/writer_system.md`，
        与既有 3 个调用点**同路径**（零改动）。
        """
        return _load("writer_system", pack=self.dir_name)


# --------------------------------------------------------------------------- #
# GenrePlugin
# --------------------------------------------------------------------------- #


class StoryGenrePlugin:
    """story 的流派插件 —— 由 `config/genres/*.toml` 数据驱动。

    ★ 与旧项目 `app/genres/base.py` 的 ABC 的关系：旧项目把
    「题材数据」与「流派行为」混在插件包里（加一个题材要写一个类）。
    本实现：**数据归 TOML，行为归本类** —— 加题材只需加一个 toml 文件。

    `plugin_mode` 参数（旧项目 `GenreManager.get(scene, flow, plugin_mode)`）
    在本设计里**消失了**：模式差异由「哪个类被实例化」承担，
    共享代码不做模式分支（契约规则 3）。
    """

    def __init__(self, genre: str | GenreCard | None = None) -> None:
        if isinstance(genre, GenreCard):
            self._card: GenreCard | None = genre
        elif genre:
            self._card = load_genre(genre)
        else:
            self._card = None

    @property
    def card(self) -> GenreCard | None:
        return self._card

    def writer_blocks(self, ctx: GenreContext) -> list[str]:
        """写作时该流派的额外约束块。

        ★ story 的"流派"有两层：
          ① 题材（`xianxia` 等，来自 `GenreCard`）—— 世界设定层
          ② 流派（learn 的 25 个修仙流派，如「悟道流」）—— 叙事套路层
        本方法目前只输出 ①（② 属阶段 2 的 learn 范围，
        旧项目权威数据在 `flows.py`）。
        """
        blocks: list[str] = []
        if self._card is not None:
            blocks.append(self._card.writer_block())
        return blocks

    def extract_hints(self) -> str:
        """给抽取器的题材专属提示。

        旧项目此处已是模式感知的（`genre_profiles.py` 的 18 题材画像 +
        12 个 flow 覆盖：`combat_level` / `has_realm` / `win_core` /
        `one_shot_ok` / `pacing` / `dialogue_floor`）。
        本实现先给最小版本：题材标签 + 常见桥段（帮抽取器识别专名）。
        """
        if self._card is None:
            return ""
        parts: list[str] = []
        if self._card.tags:
            parts.append(f"本作题材常见设定词（可能是专名）：{'、'.join(self._card.tags)}")
        if self._card.tropes:
            parts.append(f"本作题材常见桥段：{'、'.join(self._card.tropes)}")
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Estimator
# --------------------------------------------------------------------------- #


def _clamp_chapter_count(*, scale: str, mode: str, want: int) -> int:
    """按档位夹取章数（权威来源 `config/scales.toml`）。

    ★ 与旧项目的差别：旧项目有一份**硬编码兜底**
    （`_def_lo = {"fast": 3, ...}`）与 `scale_spec` 的权威值构成两份真相。
    本实现只有一份 —— 兜底就是档位本身（`ScaleSpec.chapters_lo`）。
    """
    spec = scale_spec(mode, scale)
    return spec.clamp_chapters(want)


class StoryEstimator:
    """story 的成本与时长预估（逐项对照旧项目公式，去掉 learn 专属项）。

    旧项目签名 `estimate(selected, scale, quality, overflow, story: bool)`：
    `story: bool` 是**模式分支的参数化形态**。本实现把它删掉 ——
    story/learn 各有一个类，公式差异由类承担。
    """

    #: 输出速度（tok/s）—— 可按档位注入；缺省用 `DEFAULT_SPEED`
    def __init__(self, speed: dict[str, float] | None = None) -> None:
        self._speed = dict(DEFAULT_SPEED)
        if speed:
            self._speed.update(speed)

    def _speed_for(self, tier: str) -> float:
        return self._speed.get(tier, 25.0)

    def estimate(self, scope: Scope, scale: str, quality: str = "balanced") -> Estimate:
        """估算章数 / token / 秒数。

        **不含任何 DB 访问**（见模块 docstring 的理由）。
        """
        w_speed = self._speed_for(quality)
        f_speed = self._speed_for("fast")

        # ── 章数：档位夹取（story 无知识点 → 不用 points_count 推导）──
        chapter_count = _clamp_chapter_count(
            scale=scale, mode="story", want=scope.est_chapters
        )

        # ── 逐环节拆分（story：research 恒 0，无 cards）──
        breakdown: list[dict[str, Any]] = [
            {"role": "researcher", "model": "fast", "calls": 0, "tokens": 0},
            {
                "role": "architect",
                "model": "fast",
                "calls": 1,
                # story 含「故事导演」→ 输出量 ×3
                "tokens": OUT_TOKENS["architect"] * 3,
            },
            {
                "role": "worldbuilder",
                "model": quality,
                "calls": 1,
                "tokens": OUT_TOKENS["worldbuilder"] * 2,
            },
        ]
        # story 每章输出：write + memory + review×0.5（**无 cards**）
        per_ch_out = (
            OUT_TOKENS["write"]
            + OUT_TOKENS["memory"]
            + OUT_TOKENS["review"] * 0.5
        )
        breakdown.append(
            {
                "role": "writer+memory+editor",
                "model": quality,
                "calls": round(chapter_count * CALLS_PER_CHAPTER, 1),
                "tokens": int(
                    chapter_count * (per_ch_out + PER_CHAPTER_INPUT_TOKENS)
                ),
            }
        )
        total_tokens = sum(int(b["tokens"]) for b in breakdown)

        # ── 严格串行核算（每章全链顺序执行，无并行加速假设）──
        seconds = (
            chapter_count * OUT_TOKENS["write"] / w_speed
            + chapter_count * OUT_TOKENS["memory"] / f_speed
            + chapter_count * 0.5 * OUT_TOKENS["review"] / f_speed
            + OUT_TOKENS["architect"] * 3 / f_speed
            + OUT_TOKENS["worldbuilder"] / w_speed
        )

        volumes: dict[str, Any] = {}
        spec = scale_spec("story", scale)
        if spec.is_multi_volume:
            # ★ 全书范围以档位的 chapters_lo/hi 为**唯一权威**，
            #   不用「卷数 × 每卷」推导（旧项目由此产生 900~3000 的矛盾区间）
            volumes = {
                "min_volumes": spec.vols_lo,
                "per_volume": f"{spec.per_vol_lo}~{spec.per_vol_hi} 章/卷",
                "total_chapters_range": f"{spec.chapters_lo}~{spec.chapters_hi}",
            }

        return Estimate(
            chapter_count=chapter_count,
            total_tokens=total_tokens,
            est_seconds=int(seconds),
            breakdown=tuple(breakdown),
            points_count=0,          # story 恒 0
            volumes=volumes,
        )


__all__ = [
    "OUT_TOKENS",
    "CALLS_PER_CHAPTER",
    "PER_CHAPTER_INPUT_TOKENS",
    "DEFAULT_SPEED",
    "StoryPromptPack",
    "StoryGenrePlugin",
    "StoryEstimator",
]
