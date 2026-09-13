"""Prompt 三层组装 —— 缓存命中的核心机制（对策⑦）。

## 实测依据

旧项目 writer prompt 的**逐块预算分解**（story/lean/standard，第 2 卷第 12 章）：

    总计 30,115 字符 ≈ 10.7k–13.3k tokens
      SYSTEM  4,785   ( `_WRITE_BASE` 2,313 + `anti_ai_block` 2,472 )
      USER   25,330
      稳定 ≈ 11,998 (36.5%) / 易变 ≈ 19,117 (63.5%)
      真正跨请求完全不变的只有 5,209 字符（约 17%）

**拼装顺序是缓存杀手**：每章都变的「文风范本」（U4，~800 字符）被插在
「世界观圣经」（U1，6,000 字符，卷内不变）**之后**，
把后面 19k 全部推出缓存前缀。

另一处单点最大破坏：`memory.py:1624-1629` 的 `bible_inject.select`
用 `_bible_query = f"{plot} {title}"` —— **逐章不同** → 全书最稳的圣经前缀
**在第一个块就分叉**。

## 因此本模块的硬规则

    Prompt = [稳定前缀] + [准稳定层] + [易变尾部]

    ① 稳定前缀  全书不变：角色定义 + 输出约束 + 世界观圣经
                → 目标：100% 命中缓存
    ② 准稳定层  按卷/段变化：卷级台阶 + 段目标 + 文风规则（固定文本）
                → 目标：段内 100% 命中
    ③ 易变尾部  每章变化：状态表 / 时间线 / 前文 / 本章细纲 / 字数参数
                → 无法缓存，但要尽量短

**三条禁令**（写入契约测试）：
  · 字数参数**不得**注入稳定层（旧项目 `.format(MIN_CHARS=...)` 让 system 逐章变化）
  · 时间戳 / 随机数 / 章号**不得**出现在稳定层
  · 稳定层与易变层之间**不得夹任何易变内容**
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from storyforge.infra.llm.capability import CacheMode, ModelCapability

#: 匹配易变特征的正则 —— 用于契约测试断言"稳定层里没有易变内容"
_VOLATILE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("时间戳", re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")),
    ("ISO时区", re.compile(r"\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")),
    ("章号", re.compile(r"第\s*\d+\s*章")),
    ("卷号", re.compile(r"第\s*\d+\s*卷")),
    ("字数参数", re.compile(r"字数\s*[:：]?\s*\d+|约\s*\d+\s*字|\d+\s*[~～-]\s*\d+\s*字")),
    ("UUID", re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}")),
)


class PromptLayerError(ValueError):
    """稳定层被污染时抛错 —— 缓存失效是**静默**的成本损失，必须显式拦截。"""


@dataclass
class Chunk:
    """prompt 中的一块内容，带来源标记（用于 token 归因）。"""

    source: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class PromptLayers:
    """三层 prompt 结构。

    用法：
        layers = PromptLayers()
        layers.stable.add("system", "你是资深网文作家...")
        layers.stable.add("bible", bible_text)
        layers.semi.add("volume", volume_ladder)
        layers.volatile.add("states", states_text)
        msgs = layers.assemble(capability)
    """

    stable: ChunkList = field(default_factory=lambda: ChunkList("stable"))
    semi: ChunkList = field(default_factory=lambda: ChunkList("semi"))
    volatile: ChunkList = field(default_factory=lambda: ChunkList("volatile"))

    # -- 校验 ---------------------------------------------------------------

    def validate_stability(self, *, strict: bool = True) -> list[str]:
        """检查稳定层是否真的稳定。返回违规说明列表（strict 时抛错）。

        这是把「缓存命中率」从"事后统计"变成"事前保证"的关键：
        旧项目命中率卡在 33~44% 且没人知道为什么，因为没有任何机制
        阻止易变内容进入稳定位置。
        """
        violations: list[str] = []
        for chunk in list(self.stable) + list(self.semi):
            for label, pattern in _VOLATILE_PATTERNS:
                m = pattern.search(chunk.text)
                if m:
                    violations.append(
                        f"[{chunk.source}] 含易变内容「{label}」：{m.group(0)!r} "
                        f"—— 会破坏缓存前缀（旧项目命中率只有 44% 的原因之一）"
                    )
        if violations and strict:
            raise PromptLayerError(
                "稳定层/准稳定层被易变内容污染：\n  " + "\n  ".join(violations)
            )
        return violations

    # -- 组装 ---------------------------------------------------------------

    @property
    def stable_text(self) -> str:
        return "\n\n".join(c.text for c in self.stable)

    @property
    def semi_text(self) -> str:
        return "\n\n".join(c.text for c in self.semi)

    @property
    def volatile_text(self) -> str:
        return "\n\n".join(c.text for c in self.volatile)

    def prefix_hash(self) -> str:
        """稳定前缀 hash —— 同书相邻章必须相同（CI 断言）。

        这是「缓存命中率」的可断言形式：hash 变了就一定会 miss。
        """
        return hashlib.sha256(self.stable_text.encode("utf-8")).hexdigest()[:32]

    def cacheable_prefix(self, capability: ModelCapability | None = None) -> str:
        """可缓存的前缀文本。

        · `automatic_prefix`（DeepSeek 等）：稳定层 + 准稳定层
        · `explicit_control`（Anthropic 等）：同上，但调用方需打 cache_control
        · `none`：仍按此顺序拼（便于分析），但不会命中
        """
        parts = [self.stable_text]
        if self.semi_text:
            parts.append(self.semi_text)
        return "\n\n".join(p for p in parts if p)

    def assemble(self, capability: ModelCapability | None = None) -> dict[str, str]:
        """组装成最终的两段结构。

        返回 `{"system": ..., "user": ...}`：
          · system = 稳定层（缓存友好；**不含任何逐章变化的内容**）
          · user   = 准稳定层 + 易变尾部（易变内容全部集中在这里）

        为什么把准稳定层放进 user 而不是 system：
        provider 的前缀缓存按**消息序列**匹配，system 与 user 的边界本身就是
        一个缓存单元边界。把"段级变化"的内容放进 user 开头，
        可以保持 system 完全稳定（跨卷命中），而段内 user 前缀也能命中。
        """
        self.validate_stability(strict=True)
        parts: list[str] = []
        if self.semi_text:
            parts.append(self.semi_text)
        if self.volatile_text:
            parts.append(self.volatile_text)
        return {"system": self.stable_text, "user": "\n\n".join(parts)}

    # -- 归因 ---------------------------------------------------------------

    def breakdown(self) -> dict[str, dict[str, int]]:
        """按来源统计字符数 —— 供 `ContextBreakdown` 使用（100% 覆盖）。"""
        out: dict[str, dict[str, int]] = {"chars": {}, "tokens": {}}
        for group in (self.stable, self.semi, self.volatile):
            for chunk in group:
                out["chars"][chunk.source] = out["chars"].get(chunk.source, 0) + chunk.chars
        return out

    @property
    def total_chars(self) -> int:
        return sum(c.chars for g in (self.stable, self.semi, self.volatile) for c in g)

    def layer_stats(self) -> dict[str, Any]:
        """各层体量占比 —— 用于验证"稳定段长度上限约 40%"这一实测结论。"""
        total = self.total_chars or 1
        s, m, v = len(self.stable_text), len(self.semi_text), len(self.volatile_text)
        return {
            "stable_chars": s,
            "semi_chars": m,
            "volatile_chars": v,
            "total_chars": self.total_chars,
            "stable_ratio": s / total,
            "cacheable_ratio": (s + m) / total,
        }


@dataclass
class ChunkList:
    """有序 chunk 容器（保留插入顺序，便于逐块 diff 定位缓存分叉点）。"""

    name: str
    _chunks: list[Chunk] = field(default_factory=list)

    def add(self, source: str, text: str) -> ChunkList:
        if text:
            self._chunks.append(Chunk(source=source, text=text))
        return self

    def __iter__(self):
        return iter(self._chunks)

    def __len__(self) -> int:
        return len(self._chunks)

    @property
    def text(self) -> str:
        return "\n\n".join(c.text for c in self._chunks)

    def sources(self) -> list[str]:
        return [c.source for c in self._chunks]


def diff_layers(prev: PromptLayers, cur: PromptLayers) -> dict[str, Any]:
    """比较两章的 prompt 分层 —— 定位缓存为什么没命中。

    旧项目没有这个能力：命中率低但**无法回答是哪一块变了**。
    本函数按来源逐块比对，直接指出分叉点。
    """
    def _map(layers: PromptLayers) -> dict[str, str]:
        out: dict[str, str] = {}
        for group, tag in ((layers.stable, "stable"), (layers.semi, "semi"),
                           (layers.volatile, "volatile")):
            for c in group:
                out[f"{tag}:{c.source}"] = c.text
        return out

    a, b = _map(prev), _map(cur)
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    stable_changed = [k for k in changed if k.startswith("stable:")]
    first_divergence = changed[0] if changed else None
    return {
        "changed_blocks": changed,
        "stable_changed": stable_changed,
        "prefix_hash_prev": prev.prefix_hash(),
        "prefix_hash_cur": cur.prefix_hash(),
        "prefix_stable": prev.prefix_hash() == cur.prefix_hash(),
        # 第一个分叉点 —— 它之后的所有内容都无法命中缓存
        "first_divergence": first_divergence,
    }


__all__ = [
    "Chunk",
    "ChunkList",
    "PromptLayers",
    "PromptLayerError",
    "diff_layers",
    "CacheMode",
]
