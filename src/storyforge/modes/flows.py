"""流派卡加载器 —— `config/flows/<namespace>/*.toml` → `FlowCard`。

## ★ 为什么是**两个命名空间**（用户决策，有实测依据）

旧项目有两套语义不同的 flow 数据，**不是同一回事**：

    flows.py        → learn 的 25 个**修仙流派**（`scenes=['xianxia']`）
                      23/25 从未被任何题材引用 —— learn 专属的「流派轴」
    story_flows.py  → story 的 16 个**通用叙事套路**（`channels` + `universal`）
                      16/16 全部被题材引用 —— story 的「套路轴」

两者 **2 个 ID 撞名**（`rebirth` / `system`），语义近似但**措辞不同**：

    learn 版重生流：以信息差**降维打击**；前期抢占**机缘**；同门构陷、测灵大典
    story 版重生流：以信息差与成熟心智**改写命运**；前期抢占**先机**；背叛、命运线

合并会丢掉模式专属措辞，也会让 learn 那 23 个尚未接线的混进 story 的引用域。
故分两个命名空间：

    config/flows/tropes/    ← 16 个通用套路（story，已被 `GenreCard.flows` 引用）
    config/flows/xianxia/   ← 25 个修仙流派（learn，可多选）

## 与题材的关系（两条正交的轴）

    GenreCard（题材）—— 世界观层："这个世界长什么样"（18 个）
    FlowCard（流派）—— 叙事层："怎么讲这个故事"（41 个，分两套）

一个题材可以配多个流派；learn 侧用户**可多选**（阶段 2 的目标流程）。

## 失效策略（与 `genres.py` 同口径）

  · 命名空间目录不存在 → 返回空（全新 clone 时可能尚未填充）
  · **单个**流派 id 缺失 → 抛错（不静默返回空卡）
  · TOML 语法错 / 必填字段缺失 → `FlowConfigError`
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from storyforge.config import get_settings


class FlowConfigError(ValueError):
    """流派配置不合法（语法错或字段缺失）。"""


#: 命名空间 → 语义
#:
#: ★ 命名空间是**目录名**，不是模式名的同义词 ——
#: `tropes` 是通用叙事套路（不只 story 能用），
#: `xianxia` 是修仙题材专属流派（当前 learn 用，但本质是题材维度）。
NAMESPACES: dict[str, str] = {
    "tropes": "通用叙事套路（16 个，源自旧项目 story_flows.py）",
    "xianxia": "修仙流派（25 个，源自旧项目 flows.py）",
}


@dataclass(frozen=True)
class FlowCard:
    """一个流派/套路卡。

    字段与旧项目一一对应（两套数据的**共有 6 字段** + 各自的专属字段）：

    | 字段 | 含义 | 来源 |
    |---|---|---|
    | `name` | 流派名（如「悟道流」） | 共有 |
    | `hook` | 一句话卖点 | 共有 |
    | `structure` | 叙事结构（用 `→` 串联的阶段） | 共有 |
    | `protagonist` | 主角气质/行为特征 | 共有 |
    | `narrative_devices` | 叙事装置（实测两套均为 **3 条**） | 共有 |
    | `arc_example` | **示例弧线**（84~124 字，可作架构师 few-shot） | 共有 |
    | `scenes` | 适用题材（learn 套：恒 `['xianxia']`） | 仅 learn 套 |
    | `universal` | 是否跨题材通用 | 仅 story 套 |
    | `channels` | 适用频道（male/female） | 仅 story 套 |
    """

    id: str
    name: str
    namespace: str = ""
    hook: str = ""
    structure: str = ""
    protagonist: str = ""
    narrative_devices: tuple[str, ...] = ()
    #: 示例弧线 —— 用户决策**保留**：它是给架构师的现成 few-shot
    #: （实测 84~124 字/条，25 条合计约 2,500 字；
    #: 若只在规划期使用则不会逐章进 prompt，见 `writer_block()` 的说明）
    arc_example: str = ""
    #: learn 套：适用题材（恒 `['xianxia']`）
    scenes: tuple[str, ...] = ()
    #: story 套：是否跨题材通用
    universal: bool = False
    #: story 套：适用频道
    channels: tuple[str, ...] = ()
    extra: dict[str, object] = field(default_factory=dict)

    # -- 渲染 ---------------------------------------------------------------

    def writer_block(self, *, include_example: bool = False) -> str:
        """渲染成 writer/architect 用的流派约束块。

        `include_example` 控制是否带上 `arc_example`：

          · **规划期**（架构师/细纲）→ `True`：示例弧线是现成的风格锚点，
            实测 84~124 字，成本可接受
          · **逐章写作**（writer）→ `False`：25 张卡的示例全带上会明显
            推高 token，而 hook/structure/devices 已经表达了叙事要求

        ★ 空字段**不拼空壳小节**（与 `GenreCard.writer_block` 同原则）：
        `叙事结构：`（后面什么都没有）比整节不出现更糟。
        """
        parts: list[str] = [f"【流派】{self.name}"]
        if self.hook:
            parts.append(f"卖点：{self.hook}")
        if self.structure:
            parts.append(f"叙事结构：{self.structure}")
        if self.protagonist:
            parts.append(f"主角气质：{self.protagonist}")
        if self.narrative_devices:
            parts.append(
                "叙事装置（应反复使用）：\n"
                + "\n".join(f"  · {d}" for d in self.narrative_devices)
            )
        if include_example and self.arc_example:
            parts.append(f"风格示例（供参考，不要照抄情节）：\n{self.arc_example}")
        return "\n".join(parts)

    def summary(self) -> str:
        """一行摘要（供 CLI 让用户**多选**流派时展示）。"""
        return f"{self.id:<24} {self.name:<12} {self.hook}"


#: 必填字段
#:
#: ⚠️ 只要求 `name` —— **`id` 由文件名给出**，不写进 TOML。
#: 理由：旧项目的数据里 id 是 dict 的键（不在卡片体内），
#: 移植时若把 id 也写进文件，会出现"文件名与 id 字段不一致"的第二真相
#: （本项目已记录过多份真相漂移的教训）。文件名即 id，单一来源。
_REQUIRED: tuple[str, ...] = ("name",)
#: 元组型字段
_TUPLE_FIELDS: tuple[str, ...] = ("narrative_devices", "scenes", "channels")
#: 字符串字段
_STR_FIELDS: tuple[str, ...] = ("hook", "structure", "protagonist", "arc_example")


def flows_dir(namespace: str, *, root: Path | None = None) -> Path:
    """某命名空间的目录（`<config_dir>/flows/<namespace>`）。"""
    base = root if root is not None else get_settings().config_dir / "flows"
    return base / namespace


def _card_from_dict(raw: dict, *, flow_id: str, namespace: str,
                    source: str) -> FlowCard:
    for key in _REQUIRED:
        if not str(raw.get(key, "")).strip():
            raise FlowConfigError(
                f"{source}：缺少必填字段 {key!r}（流派必须有 id 与 name）"
            )
    kwargs: dict[str, object] = {
        # ★ id 来自**文件名**（单一来源），不是 TOML 字段
        "id": flow_id,
        "name": str(raw["name"]).strip(),
        "namespace": namespace,
    }
    for key in _STR_FIELDS:
        if key in raw:
            kwargs[key] = str(raw[key])
    for key in _TUPLE_FIELDS:
        if key in raw:
            val = raw[key]
            if not isinstance(val, list):
                raise FlowConfigError(
                    f"{source}：字段 {key!r} 必须是数组，实为 {type(val).__name__}"
                )
            kwargs[key] = tuple(str(x) for x in val)
    if "universal" in raw:
        kwargs["universal"] = bool(raw["universal"])
    known = set(_REQUIRED) | set(_TUPLE_FIELDS) | set(_STR_FIELDS) | {"universal", "id"}
    extra = {k: v for k, v in raw.items() if k not in known}
    if extra:
        kwargs["extra"] = extra
    return FlowCard(**kwargs)  # type: ignore[arg-type]


def load_flow(flow_id: str, namespace: str) -> FlowCard:
    """按 id 读一个流派卡。

    缺失**抛错**（不返回空卡）—— 与 `load_genre` 同口径：
    旧项目的静默降级正是势力实体归零的成因。
    """
    if namespace not in NAMESPACES:
        raise FlowConfigError(
            f"未知命名空间 {namespace!r}；可用：{sorted(NAMESPACES)}"
        )
    d = flows_dir(namespace)
    path = d / f"{flow_id}.toml"
    if not path.exists():
        available = sorted(p.stem for p in d.glob("*.toml")) if d.exists() else []
        raise FlowConfigError(
            f"流派 {flow_id!r}（命名空间 {namespace!r}）不存在：{path}\n"
            f"可用流派：{available or '（目录为空）'}"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise FlowConfigError(f"{path.name} TOML 语法错误：{exc}") from exc
    return _card_from_dict(raw, flow_id=flow_id, namespace=namespace,
                           source=f"{namespace}/{path.name}")


def load_namespace(namespace: str) -> dict[str, FlowCard]:
    """读某命名空间的全部流派（按 id 索引）。

    目录不存在 → **空 dict**（全新 clone 时可能尚未填充）。
    """
    if namespace not in NAMESPACES:
        raise FlowConfigError(
            f"未知命名空间 {namespace!r}；可用：{sorted(NAMESPACES)}"
        )
    d = flows_dir(namespace)
    if not d.exists():
        return {}
    return {
        p.stem: load_flow(p.stem, namespace)
        for p in sorted(d.glob("*.toml"))
    }


def load_flows(ids: list[str] | tuple[str, ...], namespace: str) -> list[FlowCard]:
    """按 id 列表读多个流派（**learn 的"流派可多选"入口**）。

    ★ 顺序按传入的 `ids` 保持 —— 用户勾选的先后就是权重顺序，
    渲染进 prompt 时顺序稳定有利于缓存命中。
    """
    return [load_flow(i, namespace) for i in ids]


__all__ = [
    "FlowCard",
    "FlowConfigError",
    "NAMESPACES",
    "flows_dir",
    "load_flow",
    "load_flows",
    "load_namespace",
]
