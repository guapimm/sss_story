"""题材配置加载器 —— `config/genres/*.toml` → `GenreCard`。

## 为什么需要它（此前 `config/genres/` 是**空目录**）

现状：`genre` 只是一个**普通字符串**，透传进 bible / volume 的 prompt
（`bible_architect.py:247,256`；`volume_planner.py:244,279`）——
没有任何结构化校验，也没有流派维度的约束。

后果（旧项目实测）：
  · 三套类型词汇表冲突 → LLM 吐 `type="势力"` 判非法 → **势力实体 0 个**
  · 题材画像（`genre_profiles.py`，18 题材 + 12 flow 覆盖）与题材本身
    分散在两个文件，字段漂移

本加载器把题材**数据化**（TOML），与代码分离 —— 改题材不需要改代码。

## 数据来源（权威）

移植自旧项目 `app/story_genres.py` 的 18 个题材（字段一一对应）：
`id / channel / name / tags / world_prompt / growth_ladder / naming /
tropes / reader_expect / tone_examples / flows`。

## 失效策略

  · 文件不存在 → `FileNotFoundError`（**不静默返回空**）
  · TOML 语法错 → `GenreConfigError`（带文件名与行号）
  · 必填字段缺失 → `GenreConfigError`（列出缺哪个）
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from storyforge.config import get_settings


class GenreConfigError(ValueError):
    """题材配置不合法（语法错或字段缺失）。"""


@dataclass(frozen=True)
class GenreCard:
    """一个题材（= 世界观层面的设定骨架）。

    ★ 与 `modes.GenrePlugin`（流派**插件**）的区别：
      · `GenreCard` 是**数据**（这个题材的世界长什么样）
      · `GenrePlugin` 是**行为**（这个流派给 writer/extractor 什么约束）

    旧项目把两者混在 `genres/` 插件包里（`base.py` 的 ABC +
    `builtin/*.py` 的 4 个实现），导致"加一个题材要写一个类"。
    本设计数据归数据。
    """

    id: str
    name: str
    #: `male` / `female`
    channel: str = ""
    tags: tuple[str, ...] = ()
    #: 世界设定提示（进 bible prompt）
    world_prompt: str = ""
    #: 成长阶梯描述
    growth_ladder: str = ""
    #: 命名风格
    naming: str = ""
    #: 常见桥段
    tropes: tuple[str, ...] = ()
    #: 读者预期
    reader_expect: str = ""
    #: 基调样例
    tone_examples: tuple[str, ...] = ()
    #: 该题材可用的流派 id（对应 `flows/*.toml` 的 key）
    flows: tuple[str, ...] = ()
    #: 其它（前向兼容，不报错）
    extra: dict[str, object] = field(default_factory=dict)

    def writer_block(self) -> str:
        """渲染成 writer 用的题材约束块。

        ★ 只在**非空**时才拼对应小节 —— 空字段拼出来的
        `命名风格：`（后面什么都没有）会被模型当成"随便起名"，
        比不出现更糟（旧项目这类空壳注入实测促成了套话）。
        """
        parts: list[str] = [f"【题材】{self.name}"]
        if self.world_prompt:
            parts.append(f"世界设定：{self.world_prompt}")
        if self.growth_ladder:
            parts.append(f"成长阶梯：{self.growth_ladder}")
        if self.naming:
            parts.append(f"命名风格：{self.naming}")
        if self.tropes:
            parts.append(f"常见桥段：{'、'.join(self.tropes)}")
        if self.reader_expect:
            parts.append(f"读者预期：{self.reader_expect}")
        if self.tone_examples:
            parts.append(f"基调参考：{'、'.join(self.tone_examples)}")
        return "\n".join(parts)


#: 必填字段 —— 缺失即报错（不静默用空串）
_REQUIRED: tuple[str, ...] = ("id", "name")

#: 元组型字段（TOML 里是数组）
_TUPLE_FIELDS: tuple[str, ...] = (
    "tags", "tropes", "tone_examples", "flows",
)

#: 已知的字符串字段
_STR_FIELDS: tuple[str, ...] = (
    "channel", "world_prompt", "growth_ladder", "naming", "reader_expect",
)


def genres_dir() -> Path:
    """题材配置目录（`<config_dir>/genres`）。"""
    return get_settings().config_dir / "genres"


def _card_from_dict(raw: dict, *, source: str) -> GenreCard:
    for key in _REQUIRED:
        if not str(raw.get(key, "")).strip():
            raise GenreConfigError(
                f"{source}：缺少必填字段 {key!r}（题材必须有 id 与 name）"
            )
    known = set(_REQUIRED) | set(_TUPLE_FIELDS) | set(_STR_FIELDS)
    kwargs: dict[str, object] = {
        "id": str(raw["id"]).strip(),
        "name": str(raw["name"]).strip(),
    }
    for key in _STR_FIELDS:
        if key in raw:
            kwargs[key] = str(raw[key])
    for key in _TUPLE_FIELDS:
        if key in raw:
            val = raw[key]
            if not isinstance(val, list):
                raise GenreConfigError(
                    f"{source}：字段 {key!r} 必须是数组，实为 {type(val).__name__}"
                )
            kwargs[key] = tuple(str(x) for x in val)
    extra = {k: v for k, v in raw.items() if k not in known}
    if extra:
        kwargs["extra"] = extra
    return GenreCard(**kwargs)  # type: ignore[arg-type]


def load_genre(genre_id: str, *, directory: Path | None = None) -> GenreCard:
    """按 id 读一个题材（`<id>.toml`）。

    缺失**抛错** —— 不返回空卡。旧项目的静默降级
    （"判定不在枚举内 → 降级为「其他」"）正是势力实体归零的成因。
    """
    d = directory or genres_dir()
    path = d / f"{genre_id}.toml"
    if not path.exists():
        available = sorted(p.stem for p in d.glob("*.toml")) if d.exists() else []
        raise GenreConfigError(
            f"题材 {genre_id!r} 不存在：{path}\n"
            f"可用题材：{available or '（目录为空）'}"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise GenreConfigError(f"{path.name} TOML 语法错误：{exc}") from exc
    return _card_from_dict(raw, source=path.name)


def load_all_genres(*, directory: Path | None = None) -> dict[str, GenreCard]:
    """读全部题材（按 id 索引）。

    目录不存在 → **返回空 dict**（不是报错）：全新 clone 时
    `config/genres/` 可能尚未填充，而"没有题材"是合法状态
    （BIBLE 阶段会要求调用方显式指定题材）。
    但**单个文件缺失**才报错（见 `load_genre`）。
    """
    d = directory or genres_dir()
    if not d.exists():
        return {}
    return {p.stem: load_genre(p.stem, directory=d) for p in sorted(d.glob("*.toml"))}


__all__ = [
    "GenreCard",
    "GenreConfigError",
    "genres_dir",
    "load_genre",
    "load_all_genres",
]
