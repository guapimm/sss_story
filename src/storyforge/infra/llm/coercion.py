"""模型输出的**形状归一** —— 与 schema 定义分离的通用工具层。

## 为什么单独成模块

这些函数是**通用**的（任何 schema 都可能用到），与具体的
`BibleOut` / `SeriesPlanOut` 定义无关。混在 `schemas.py` 里会让它
无谓地膨胀（当年的触发点：加了这一层后 `schemas.py` 从 640 行涨到 802 行，
撞上规则 10 的 800 行上限；**该文件上限已于 2026-09-26 取消**，
但"契约 vs 归一"的职责分离依然成立）。

拆出来之后：
  · `schemas.py` 只放**契约**（字段与校验）
  · `coercion.py` 只放**归一**（形状转换）
  两者职责清晰，各自都不会因为对方增长而超限。

## 为什么需要归一这一层（实测驱动，不是防御性编程）

真实运行（小米 MiMo）暴露出**同一类失败反复出现**：模型给出的内容是对的，
但**形状**与 schema 不一致：

    · `terms`   → 模型给 `{"值夜人": "含义"}`（字典），schema 要 `list[str]`
    · `rules`   → 某一项给成 `{"规则": "…", "例外": "…"}`（对象）
    · 卷号       → 模型给 `volume_number`，schema 要 `volume_no`
    · 里程碑     → 模型给 `[8, 16]`（裸章号），schema 要 `[{"chapter_no": 8}]`
    · 里程碑     → 模型给 `"第6章：潜入上海…"`（带描述的字符串）

后果不是"抽得差"而是**整条链路挂掉**：`complete_structured` 三次修复重试
全失败 → 节点放弃 → 规划中断。而模型每次都在输出**信息完整**的结果。

这与旧项目 `entity_classify` 空输出 100% 是同一形态：
**schema 与 prompt 的表述有缝，模型选了它更自然的一边，另一边永远拒它。**

## 处理原则：对输入宽容，对输出严格

归一**只做形状转换，不做语义猜测**；归一后仍走同一套校验器。
绝不因为"能转"就放过真正缺字段的情况（缺就是缺，继续报错）。
"""

from __future__ import annotations

from typing import Any

#: 明确同义的字段名映射（模型实测偏好 → 本项目的规范名）。
#:
#: ⚠️ 只登记**确定同义**的写法。宽泛模糊匹配会把真正缺失的字段也"补"上，
#: 那会让校验器失去意义（比直接失败更危险）。
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "volume_no": ("volume_number", "volume_index", "vol_no", "卷号", "卷序号"),
    "chapter_count": ("chapters", "chapter_total", "num_chapters", "章数"),
    "target_chapters": ("target_total_chapters", "total_chapters", "目标章数"),
    "total_volumes": ("volume_count", "num_volumes", "总卷数"),
    "logline": ("summary", "one_liner", "一句话梗概", "简介"),
    "new_locations": ("locations", "新地图", "新地点"),
    "new_factions": ("factions", "新势力"),
    "escalation_from_prev": ("escalation", "升级点", "上卷升级"),
    "old_map_closure": ("map_closure", "旧地图收尾"),
    "power_ceiling": ("ceiling", "力量上限"),
    "core_conflict": ("conflict", "核心矛盾"),
}


def first_key(d: dict[str, Any], *names: str) -> Any:
    """按候选名取第一个非 None 的值（用于字段名漂移）。"""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return None


def apply_field_aliases(data: Any) -> Any:
    """把同义字段名补成规范名（`FIELD_ALIASES` 的落地）。

    只**补**不**改**：如果规范名已存在，保留原值不动。
    """
    if not isinstance(data, dict):
        return data
    d = dict(data)
    for canonical, aliases in FIELD_ALIASES.items():
        if canonical not in d:
            v = first_key(d, *aliases)
            if v is not None:
                d[canonical] = v
    return d


def coerce_str_list(v: Any) -> Any:
    """把模型给的「字符串列表的各种等价形状」归一成 `list[str]`。

    支持：
      · `["a——b", "c——d"]`                    原样
      · `{"a": "b", "c": "d"}`                 → `["a——b", "c——d"]`（多项对照）
      · `[{"term": "a", "meaning": "b"}]`      → `["a——b"]`
      · `[{"term": "孤词"}]`                    → `["孤词"]`（单字段取**值**）
      · `[{"规则": "x", "例外": "y"}]`          → `["规则：x；例外：y"]`（多字段）
    """
    if v is None:
        return []
    if isinstance(v, dict):
        # ★ 顶层字典 = 「多项对照」：每个键值对是**一条**
        #   （实测 terms 的天然写法：{"值夜人": "含义", "朱批": "含义"}）
        #   这与「列表里的多字段对象」（一条规则的多个侧面）是**两种形状**，
        #   不能混为一谈 —— 实测踩到过。
        return [
            f"{k}——{val}" if val not in (None, "") else str(k)
            for k, val in v.items()
        ]
    if isinstance(v, list):
        out: list[str] = []
        for item in v:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(_dict_item_to_text(item))
            else:
                out.append(str(item))
        return out
    return v


def _dict_item_to_text(item: dict[str, Any]) -> str:
    """把**列表里的一个 dict 项**拼成一行文本。

    两种形状，语义不同：
      · 「主名 + 解释」→ `主名——解释`（术语对照）
      · 「多字段」      → `键：值；键：值`（一条规则的多个侧面，如实测
        `{"规则": "…", "例外": "…"}`）
    """
    if not item:
        return ""
    pairs = [(str(k), "" if v in (None, "") else str(v)) for k, v in item.items()]
    if len(pairs) == 1:
        k, val = pairs[0]
        # 单字段 dict：**值**才是内容，键只是标签
        return val or k

    # 已知的「主名 + 解释」配对（术语场景）
    key = first_key(item, "term", "name", "key", "id", "标题", "名称", "规则")
    val = first_key(item, "meaning", "desc", "description",
                    "value", "含义", "说明")
    if key is not None and val is not None and len(pairs) == 2:
        return f"{key}——{val}"
    return "；".join(f"{k}：{val}" if val else k for k, val in pairs)


def coerce_records(v: Any, *field_names: str) -> Any:
    """把「裸值列表」补成「对象列表」。

    实测：里程碑被模型写成 `[8, 16, 24]`（卷内章号），而 schema 要
    `[{"chapter_no": 8}, ...]`。信息完全等价 —— 裸值就是第一个字段。

    ★ 字符串也支持（`ReviewResultOut.issues` 实测需要）：
    模型把问题写成**一句话描述**而不是 `{severity, category, description}`
    对象是很自然的读法 —— 它**把最重要的信息（问题是什么）给了**，
    只是没按字段拆开。直接拒绝会让**整章正文重写失败**（实测踩到：
    `style_critic` 三次全败 → 抛异常中断整个 `write`）。

    ⚠️ 只在**全部元素都是标量或字符串**时才转换；一旦出现 dict 就原样交给
    Pydantic（那说明模型用了别的形状，应当正常报错而不是猜）。
    """
    if not isinstance(v, list) or not v or not field_names:
        return v
    if not all(isinstance(x, (int, float, str)) and not isinstance(x, bool)
               for x in v):
        return v
    primary = field_names[0]
    out: list[dict[str, Any]] = []
    for x in v:
        if isinstance(x, str):
            s = x.strip()
            if not s:
                continue
            out.append({primary: s})
        else:
            out.append({primary: int(x)})
    return out


__all__ = [
    "FIELD_ALIASES",
    "first_key",
    "apply_field_aliases",
    "coerce_str_list",
    "coerce_records",
]
