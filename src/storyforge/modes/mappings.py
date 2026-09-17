"""平替修辞种子加载器 —— `config/mappings/*_seed.toml` → `TermMapping`。

## 「种子」的定位（重要，别当成最终稿）

旧项目 `templates/scenes.py:14` 明写：

> 名称与阶梯必须**按本书自创**，禁止套用炼气筑基金丹等现成表。

所以平替修辞**本应由 Agent 按知识点 + 流派生成**（阶段 2-3 的生成步骤）。
本加载器读的是那 10 条**已验证可用的映射范式**，用途只有两个：

  ① **few-shot**：告诉生成平替修辞的 Agent「映射的粒度与风格长什么样」
  ② **起点**：无 API key / 离线时也能走完 L-2 确认流程

⚠️ 因此生成流程**不得**直接把种子当结果返回 —— 否则每本书的
平替修辞都一样，正是旧项目"禁止套用现成表"要避免的。
`seed_only=True` 的路径必须在界面上显式标注"这是种子，需人工调整"。

## 与 `modes/flows.py` 的关系

流派卡（`FlowCard`）给的是**叙事套路**（怎么讲故事）；
平替修辞给的是**术语说法**（知识点叫什么）。两者正交，
共同构成 writer 的 learn 侧约束。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from storyforge.config import get_settings
from storyforge.domain.learning import TermMapping, parse_mapping_rule


class MappingConfigError(ValueError):
    """平替修辞配置不合法。"""


@dataclass(frozen=True)
class MappingSeed:
    """一份平替修辞种子（题材级）。"""

    genre: str
    description: str = ""
    style_prompt: str = ""
    #: ★ 种子映射（`is_seed=True` 提醒调用方这不是最终稿）
    mappings: tuple[TermMapping, ...] = ()
    #: 文风示例（few-shot 用）
    style_samples: tuple[str, ...] = ()

    @property
    def is_seed(self) -> bool:
        return True

    def render_block(self) -> str:
        """渲染成 writer 用的约束块（对照旧项目四处注入的措辞）。"""
        if not self.mappings:
            return ""
        lines = [
            "【术语映射规则 —— 正文中的知识点必须按此转换为世界内说法】"
        ]
        lines += [f"- {m.term} → {m.replacement}" for m in self.mappings]
        return "\n".join(lines)


def mappings_dir() -> Path:
    return get_settings().config_dir / "mappings"


def load_mapping_seed(genre: str = "xianxia", *,
                      directory: Path | None = None) -> MappingSeed:
    """读某题材的平替修辞种子。

    缺失 → 抛错（不返回空表：空表会让 writer 拿不到任何术语约束，
    正文会直出原术语 —— 而这是审校层硬拦的问题）。
    """
    d = directory or mappings_dir()
    path = d / f"{genre}_seed.toml"
    if not path.exists():
        available = sorted(p.name for p in d.glob("*_seed.toml")) if d.exists() else []
        raise MappingConfigError(
            f"平替修辞种子不存在：{path}\n可用：{available or '（目录为空）'}"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MappingConfigError(f"{path.name} TOML 语法错误：{exc}") from exc

    mappings: list[TermMapping] = []
    for i, item in enumerate(raw.get("mappings") or []):
        if not isinstance(item, dict):
            # 兼容裸字符串写法（旧项目格式）
            parsed = parse_mapping_rule(str(item))
            if parsed:
                mappings.append(parsed)
            continue
        term = str(item.get("term", "")).strip()
        repl = str(item.get("replacement", "")).strip()
        if not term or not repl:
            raise MappingConfigError(
                f"{path.name} 第 {i + 1} 条映射缺 term 或 replacement"
            )
        mappings.append(
            TermMapping(
                term=term,
                replacement=repl,
                rationale=str(item.get("rationale", "")),
            )
        )

    samples: list[str] = []
    sample_file = d / f"{genre}_style_samples.md"
    if sample_file.exists():
        text = sample_file.read_text(encoding="utf-8")
        # 去掉注释头，按 `---` 分段
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        )
        samples = [s.strip() for s in body.split("\n---\n") if s.strip()]

    return MappingSeed(
        genre=str(raw.get("genre", genre)),
        description=str(raw.get("description", "")),
        style_prompt=str(raw.get("style_prompt", "")),
        mappings=tuple(mappings),
        style_samples=tuple(samples),
    )


__all__ = [
    "MappingSeed",
    "MappingConfigError",
    "mappings_dir",
    "load_mapping_seed",
]
