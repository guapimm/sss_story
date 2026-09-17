"""篇幅档位加载器 —— `config/scales.toml` → `ScaleSpec`。

## 为什么需要它

`Estimator` 的章数推导依赖档位（`chapters_lo/hi`、`per_vol_lo/hi`、`vols_lo`），
而旧项目这些值**硬编码在 `agents/architect.py` 的函数体内**
（`{"fast": 3, "standard": 10, "grand": 100, "ultra": 300}` 散落多处），
与本文件里的权威值构成**两份真相** —— 实测已经漂移过一次
（`_def_lo` / `_def_hi` 的兜底值与 `scale_spec` 不一致）。

本加载器把档位数据化，并**只暴露一份**。

## 结构（`config/scales.toml`）

    [grand]              # 共享档位（两模式相同）
    [story.fast]         # 分模式档位
    [learn.fast]

查询用 `scale_spec(mode, scale)` —— 先找 `[<mode>.<scale>]`，
找不到再回落到顶层 `[<scale>]`（共享档位）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from storyforge.config import get_settings


class ScaleConfigError(ValueError):
    """档位配置不合法。"""


@dataclass(frozen=True)
class ScaleSpec:
    """一个篇幅档位。"""

    scale: str
    label: str = ""
    desc: str = ""
    chapters_lo: int = 0
    chapters_hi: int = 0
    vols_lo: int = 1
    vols_hi: int = 1
    per_vol_lo: int = 0
    per_vol_hi: int = 0
    #: 分段长度（0 = 不分段）
    segment: int = 0
    typical_chapters: int = 0
    #: 是否可续接扩写
    expandable: bool = True
    #: `divergent`（留接口） / `closed`（终卷强收束）
    ending: str = ""
    #: 进 prompt 的篇幅规则
    rule: str = ""
    #: 节奏配点要求
    pacing: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def is_multi_volume(self) -> bool:
        return self.vols_hi > 1 or self.vols_lo > 1

    def clamp_chapters(self, want: int) -> int:
        """把期望章数夹到本档位区间内。

        ★ 夹取是**刻意的**：旧项目 `chapter_count` 有时为 0
        （`est_ch` 全为 0 时），会推出"0 章的书"。下限至少 1。
        """
        lo = max(1, self.chapters_lo)
        hi = max(lo, self.chapters_hi)
        return max(lo, min(hi, max(1, int(want))))


_KNOWN: frozenset[str] = frozenset(
    {
        "label", "desc", "chapters_lo", "chapters_hi", "vols_lo", "vols_hi",
        "per_vol_lo", "per_vol_hi", "segment", "typical_chapters", "expandable",
        "ending", "rule", "pacing",
    }
)


def scales_path() -> Path:
    return get_settings().config_dir / "scales.toml"


@lru_cache(maxsize=8)
def _load_raw(path_str: str, mtime_ns: int) -> dict:
    """读并解析 TOML。

    ⚠️ 缓存键里带 `mtime_ns` —— 否则改了文件后测试仍读旧值
    （本项目已有 `config.py` 的同类教训：import 期冻结）。
    """
    p = Path(path_str)
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ScaleConfigError(f"{p.name} TOML 语法错误：{exc}") from exc


def _raw() -> dict:
    p = scales_path()
    if not p.exists():
        raise ScaleConfigError(
            f"档位配置不存在：{p}\n"
            f"（这是 Estimator 推导章数的权威来源，缺失则无法预估）"
        )
    return _load_raw(str(p), p.stat().st_mtime_ns)


def _spec_from(scale: str, raw: dict) -> ScaleSpec:
    kwargs: dict[str, object] = {"scale": scale}
    for k, v in raw.items():
        if k in _KNOWN:
            kwargs[k] = v
    extra = {k: v for k, v in raw.items() if k not in _KNOWN}
    if extra:
        kwargs["extra"] = extra
    return ScaleSpec(**kwargs)  # type: ignore[arg-type]


def _mode_names(data: dict) -> frozenset[str]:
    """TOML 里出现过的**模式**名 —— 即「含子表的顶层键」。

    判据：`[story.fast]` 解析成 `{"story": {"fast": {...}}}`，
    所以"值仍是 dict 的 dict"就是模式表，而 `[grand]` 的字段值是标量。

    ⚠️ 不写死 `("story","learn")`：learn 上线时不该再改这里；
    但也不能把所有顶层 dict 都当模式（那会把共享档位误判成模式）。
    """
    return frozenset(
        k
        for k, v in data.items()
        if isinstance(v, dict) and any(isinstance(x, dict) for x in v.values())
    )


def _scale_names(data: dict, mode: str) -> set[str]:
    """某模式可用的档位名 = 共享档位（顶层标量字段组）+ 该模式专属档位。"""
    modes = _mode_names(data)
    shared = {k for k in data if k not in modes}
    own = set((data.get(mode) or {}).keys())
    return shared | own


def scale_spec(mode: str, scale: str) -> ScaleSpec:
    """取某模式下的档位。先找 `[<mode>.<scale>]`，回落到顶层 `[<scale>]`。

    两边都没有 → 抛错（**不返回默认档**：静默回落会让 25 章的书
    按 400 章的档位规划 —— 旧项目"400 章全挤第 1 卷"的同类风险）。
    """
    data = _raw()
    per_mode = data.get(mode)
    if isinstance(per_mode, dict) and scale in per_mode:
        return _spec_from(scale, per_mode[scale])
    if scale in _scale_names(data, mode) and isinstance(data.get(scale), dict):
        return _spec_from(scale, data[scale])
    raise ScaleConfigError(
        f"档位 {scale!r}（模式 {mode!r}）不存在。"
        f"可用档位：{sorted(_scale_names(data, mode))}"
    )


def all_scales(mode: str) -> dict[str, ScaleSpec]:
    """某模式可用的全部档位（含共享档位）。

    ⚠️ 不得把**另一个模式的名字**当成档位名 —— 实测踩到：
    初版 `all_scales("story")` 返回了 `['fast','grand','learn',...]`，
    因为 `[learn]` 是个顶层 dict 就被当成档位了。
    """
    data = _raw()
    return {s: scale_spec(mode, s) for s in sorted(_scale_names(data, mode))}


__all__ = [
    "ScaleSpec",
    "ScaleConfigError",
    "scale_spec",
    "all_scales",
    "scales_path",
]
