"""枚举 → prompt 文本的渲染 —— **单一来源**。

## 为什么必须只有一处

旧项目最严重的枚举冲突：

    memory.py 阈值表          5 档（轻微/轻/中/重/极重）
    post_processor.py prompt  3 档（轻/重/极重）
    → 「轻微」「中」永远不被产生 → 阈值查不到 → 该损伤**永不催促**
    → 实测书 24：最重的「神魂受创（伤势·极重）」恰恰是唯一不催的那条

本模块的 `enum_values` 让 schema 的 `Field(description=...)` 直接由枚举生成，
于是**枚举 / schema / prompt 三者同源，不可能漂移**。

拆出独立模块是因为它被 `schemas.py` 与 `extraction_schemas.py` 共用 ——
放在任一个里都会让另一个反向依赖（违反分层单向依赖）。
"""

from __future__ import annotations

from enum import EnumMeta


def enum_values(enum_cls: EnumMeta) -> str:
    """把枚举渲染成 prompt 可用的取值列表（`a | b | c`）。"""
    return " | ".join(str(e.value) for e in enum_cls)  # type: ignore[attr-defined]


__all__ = ["enum_values"]
