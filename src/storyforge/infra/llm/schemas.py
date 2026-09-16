"""LLM 结构化输出的 Pydantic schema —— **单一来源**。

## 为什么 schema 必须是单一来源

旧项目最严重的枚举冲突：

    memory.py 阈值表          5 档（轻微/轻/中/重/极重）
    post_processor.py prompt  3 档（轻/重/极重）
    → 「轻微」「中」永远不被产生 → 阈值查不到 → 该损伤**永不催促**
    → 实测书 24：最重的「神魂受创（伤势·极重）」恰恰是唯一不催的那条

本模块的原则：
  · **枚举值从 `domain/enums.py` 取**，prompt 文本由枚举**生成**
  · schema 里的 `Field(description=...)` 就是给模型的指令
  · 三者（枚举 / schema / prompt）同源，不可能漂移

## 为什么每个 schema 都要有 description

旧项目 `post_processor.py` 的单条 system prompt **超过 10,000 字符**，
一次要求抽取 11 类信息（cards / summary / state_updates / deaths /
relationship_updates / events / plot_progress / claims / schema_delta /
hang / entities）→ `entity_classify` 空输出 **100%**。

本模块把职责拆开（对策⑥），每个节点只抽一类，schema 自带约束说明。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from storyforge.domain.enums import (
    BeatType,
    EntityKind,
    HealthAction,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
    LifecycleState,
    RelationKind,
    RelationSentiment,
)
from storyforge.infra.llm.coercion import (
    apply_field_aliases,
    coerce_records,
    coerce_str_list,
)


def _enum_values(enum_cls) -> str:
    """把枚举渲染成 prompt 可用的取值列表（**单一来源的落地方式**）。"""
    return " | ".join(e.value for e in enum_cls)


# --------------------------------------------------------------------------- #
# 圣经
# --------------------------------------------------------------------------- #


class BibleOut(BaseModel):
    """世界观圣经（对策：L0 稳定前缀，全书不变）。"""

    title: str = Field(description="书名")
    worldview: str = Field(description="世界观设定：世界结构、力量体系、时代背景")
    rules: list[str] = Field(description="世界硬规则（不可违反的设定）", default_factory=list)
    power_system: str = Field(default="", description="力量/能力体系说明")
    era: str = Field(default="", description="时代与地域背景")
    tone: str = Field(default="", description="整体基调与文风")
    terms: list[str] = Field(
        default_factory=list,
        description='术语与专名对照。每条形如「世界内说法——含义」。'
        '必须输出**字符串数组**，例如 ["值夜人——承担夜间值守的人"]，'
        '不要输出 {"值夜人": "承担夜间值守的人"} 这样的对象',
    )

    @field_validator("rules", mode="before")
    @classmethod
    def _normalize_rules(cls, v: Any) -> Any:
        """`rules` 也接受对象形状（实测 `rules.17` 不是字符串）。

        ## 实测（书 11 第 2 轮，3 次尝试全部失败）

            "rules": [..., {"rule": "…", "exception": "…"}]   ← 第 17 项是对象

        模型在写到最后几条规则时"想补充细节"，于是把一条规则写成了
        `{"规则": ..., "例外": ...}` 这种带子字段的对象。

        `list[str]` 直接拒它 → 三次修复重试全失败 → **整本书的圣经生成失败**。

        与 `terms` 同源的修法：**归一成文本，而不是拒绝** ——
        把对象的值拼成一行「键：值；键：值」。信息不丢，格式统一。
        """
        return coerce_str_list(v)

    @field_validator("terms", mode="before")
    @classmethod
    def _normalize_terms(cls, v: Any) -> Any:
        """把模型**天然想给的形状**归一成 `list[str]`（详见 `coerce_str_list`）。

        实测（小米 MiMo，连续 3 次）：prompt 说「格式：世界内说法——含义」，
        模型就给出 `{"值夜人": "承担夜间值守的人", ...}` —— 这是**完全合理**的读法，
        信息量一模一样。而 `list[str]` 会拒掉它，于是三次重试全失败、节点放弃。

        修法不是"把 prompt 写得更凶"（那只是把冲突推给模型），
        而是**接受两种等价形状**：信息相同就不该有理由拒绝。
        """
        return coerce_str_list(v)

    @model_validator(mode="before")
    @classmethod
    def _relocate_misnested_fields(cls, data: Any) -> Any:
        """把**塞错字段**的内容自动搬回它该在的顶层字段。

        ## 实测（小米 MiMo，间歇性出现）

        模型有时把本该是顶层字段的内容**塞进 `rules` 数组**：

            "rules": [
              ...真实的规则...,
              "era：永恒黄昏时代。档案馆内无自然光照变化…",
              "tone：冷峻、压抑、充满未知威胁的悬疑基调…"
            ]

        后果是 `era` / `tone` / `terms` **三个顶层字段全为空**。

        ## 为什么改成"自动归位"而不是"报错重试"

        原先的实现（`@field_validator("rules")` 抛 ValueError）会在这种情况下
        拒绝整份输出 → 三次修复重试 → 若模型仍这么写就**整本书的圣经生成失败**。

        实测这是**间歇性**的：同一本书连续 3 次调用全部正常通过，
        但多书测试那一轮恰好撞上，`bible:misnest` 直接卡死整条链路。

        「间歇性结构错位」不该靠重试硬扛 —— 与本文件对 `terms` / `rules`
        形状漂移的处理原则一致：**信息不丢就归位，而不是拒绝**。
        （对输入宽容、对输出严格：归位后仍走同一套校验器。）

        ## 归位规则（只认确定的前缀，不猜）

        条目形如 `<字段名>：<内容>` / `<字段名>: <内容>`，且字段名在本模型的
        已知字段集合内 → 摘出来放回对应字段，并从 `rules` 移除。

        - `era` / `tone`（标量）：内容是字符串 → 直接赋值
        - `terms`（数组）：内容是数组或「说法——含义」串 → 逐条拆开
          （实测模型会写成 `"terms： ["` 这种被截断的样子，那种残缺条目**丢弃**）

        字段名与形状都取自 `_BIBLE_RELOCATABLE` —— **单一来源**，
        避免"改了集合忘了改分支"这类漂移。
        """
        if not isinstance(data, dict):
            return data
        rules = data.get("rules")
        if not isinstance(rules, list) or not rules:
            return data

        # 目标字段 → 收集到的内容
        relocated: dict[str, list[str]] = {}
        kept: list[Any] = []
        for item in rules:
            head, sep, tail = _split_field_label(item)
            if sep and head in _BIBLE_RELOCATABLE:
                relocated.setdefault(head, []).append(tail)
            else:
                kept.append(item)

        if not relocated:
            return data

        d = dict(data)
        d["rules"] = kept
        for field, values in relocated.items():
            if _BIBLE_RELOCATABLE[field] == "list":
                # terms 可能是数组、多行、或「说法——含义」串
                if d.get(field):
                    continue
                items: list[str] = []
                for v in values:
                    items.extend(_split_terms_text(v))
                if items:
                    d[field] = items
                continue
            # 标量：只在顶层字段为空时归位 —— 已有内容不覆盖（顶层更可信）
            if (d.get(field) or "").strip():
                continue
            joined = " ".join(v for v in values if v).strip()
            if joined:
                d[field] = joined
        return d

    @model_validator(mode="after")
    def _reject_missing_core_fields(self) -> BibleOut:
        """结构自检：**核心字段不得为空**。

        ⚠️ 这里只检查 `era` —— 它是圣经的必需要素（时代与地域），
        而且错位后必然为空（内容被搬到 `rules` 里去了）。

        **不检查 `terms`**：术语对照是"锦上添花"，缺失只影响用词一致性，
        不该让整本书的圣经生成失败。`validate_bible` 里有独立的下限检查
        （`_MIN_TERMS`），那属于**质量闸门**，不是**结构必需**。
        把两者分开，避免一个可选字段卡死整条链路。
        """
        if not (self.era or "").strip():
            raise ValueError(
                "字段 era 为空 —— 实测模型会把它的内容写进 rules，"
                "导致顶层字段空缺（已尝试自动归位但未找到内容）。"
                "请确保 era 填上时代与地域背景。"
            )
        return self

#: 圣经里"可以被误塞进 rules"的顶层字段 → 该字段的形状（`"str"` / `"list"`）。
#:
#: ⚠️ 登记条件（两条都要满足），否则宁可不登记：
#:   1. **确定会错位** —— 实测出现过，或有同源证据；
#:   2. **能区分"字段标签"与"真规则内容"** —— 见 `_split_field_label`：
#:      只认「行首恰好是字段名 + 冒号」的形式。
#:
#: 为什么不必登记 `title` / `worldview` / `power_system`：
#: 它们的标签同样是行首字段名，但真规则里出现「世界观：…」的概率远高于
#: 「era：…」（前者是中文常用词）。**只登记实测过的**，
#: 是「对输入宽容」与「不猜」之间的分界 —— 猜错会把真规则搬走，比不搬更糟。
_BIBLE_RELOCATABLE: dict[str, str] = {
    "era": "str",
    "tone": "str",
    "terms": "list",
}


def _split_field_label(item: Any) -> tuple[str, str, str]:
    """把 `"era：内容"` 拆成 `("era", "：", "内容")`。

    只认**行首就是字段名**的形式（真规则不会以 `era：` 开头）。
    返回 `(head, sep, tail)`；`sep` 为空表示这不是字段标签。

    ★ 也认 `{"era": "永恒黄昏时代"}` 这种**单键对象** ——
    实测模型错位时并不总是写成字符串，同一个"塞错字段"的毛病
    换个形状（对象）就认不出来，等于只修了一半。
    （单键限制与 `coerce_str_list` 同一原则：键是标签、值是内容。）
    """
    if isinstance(item, dict) and len(item) == 1:
        key, value = next(iter(item.items()))
        head_clean = str(key).strip().lower()
        if head_clean in _BIBLE_RELOCATABLE:
            if isinstance(value, list):
                return head_clean, "：", "；".join(str(v) for v in value)
            return head_clean, "：", str(value).strip()
        return "", "", str(item)
    s = str(item).strip()
    for sep in ("：", ":"):
        head, found, tail = s.partition(sep)
        if found:
            head_clean = head.strip().lower()
            # 字段名不该太长（防把普通句子误判）
            if len(head_clean) <= 12:
                return head_clean, found, tail.strip()
            return "", "", s
    return "", "", s


def _split_terms_text(text: str) -> list[str]:
    """把 `terms` 的错位内容拆成条目列表。

    实测模型会写出这些形态：
        · `["值夜人——承担夜间值守的人", "朱批——改判之权"]`  （数组）
        · `"值夜人——承担夜间值守的人；朱批——改判之权"`      （分号分隔）
        · `"["`                                              （被截断的残片 → 丢弃）
    """
    s = (text or "").strip()
    if not s:
        return []
    # 残片（如被截断的 "["、"{" ）直接丢 —— 留着只会污染术语表
    if s in ("[", "]", "{", "}", "[ ]", "{}"):
        return []
    # 看起来像 JSON 数组：尝试解析
    if s.startswith("["):
        try:
            import json as _json  # noqa: PLC0415

            parsed = _json.loads(s if s.endswith("]") else s + "]")
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (ValueError, TypeError):
            pass
    # 分号 / 换行分隔
    parts = [p.strip() for p in re.split(r"[；;\n]+", s) if p.strip()]
    return parts


# --------------------------------------------------------------------------- #
# 卷级升级台阶（对策②核心）
# --------------------------------------------------------------------------- #


class MilestoneOut(BaseModel):
    """卷内里程碑。

    ⚠️ `chapter_no` 是**本卷内**的章序号（从 1 开始），不是全书号。

    为什么：模型不知道各卷的全局偏移（那是代码按各卷 chapter_count 累加出来的），
    让它自己累加必然出错 —— 一旦第一卷的章数估偏，后续所有里程碑整体偏移。
    这与决策 D-15「派生不存储」同一原则：**模型给局部，代码算全局**。
    """

    chapter_no: int = Field(
        description="里程碑在**本卷内**的章序号（从 1 开始，不超过本卷 chapter_count）。"
        "给卷内序号即可，全书号由系统自动换算"
    )
    key: str = Field(default="", description="里程碑标识（可留空，代码会补）")
    description: str = Field(default="", description="里程碑内容一句话")

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_int(cls, data: Any) -> Any:
        """接受三种等价写法，全部归一到 `{"chapter_no": N, "description": ...}`：

          · `{"chapter_no": 8, "description": "..."}`   规范形式
          · `8`                                          裸卷内章号
          · `"第6章：潜入上海，追踪知识窃取者踪迹"`       ★ 实测最常出现的形态
          · `"8"`                                        纯数字字符串

        ★ 字符串形态为什么必须接受：实测小米 MiMo **两次**都选择把里程碑写成
        「第N章：描述」。这不是模型犯错 —— 那是中文小说里表达里程碑最自然的
        写法，而且它**把章号和描述一起给了**，信息比规范形式还全。

        旧项目 `entity_classify` 空输出 100% 是同一形态：schema 只认一种写法，
        模型稳定地选另一种，于是一路失败到底。修法是接受等价形状，
        而不是反复要求模型改（它每次都改不动，白烧 token）。

        `key` 由代码补（见 `series_plan_from_out`），不要求模型编标识符。
        """
        if isinstance(data, (int, float)):
            return {"chapter_no": int(data)}
        if isinstance(data, str):
            import re as _re  # noqa: PLC0415

            # 优先取「第N章」里的 N（模型最常这么写）
            m = _re.search(r"第\s*(\d+)\s*章", data)
            if not m:
                m = _re.search(r"(\d+)", data)
            if m:
                desc = _re.sub(r"^第\s*\d+\s*章\s*[:：、.。]?\s*", "", data).strip()
                return {"chapter_no": int(m.group(1)), "description": desc}
        return data


class VolumePlanOut(BaseModel):
    """一卷的**升级台阶**。

    ## 为什么这些字段必填

    旧项目把卷信息塞在 `books.outline` 的 JSON 列里，后续卷只有占位目标
    （`blueprint="missing"`），导致 **400 章全挤第 1 卷** ——
    而第 1 卷的 vol_goal 是「在一栋楼里读透三十七条」。

    实测地理熵：书 24/23/18 的**后半本新增地点 0/1/5**；
    书 23 的 400 章场景 token 主体是「门/电梯/楼层/走廊」。

    根因还包括**多层代码显式禁止换图**（8 处）：
      `architect.py:397/401/372`、`writer.py:258`、`writer_rules.py:374`、
      `editor.py:243/106` → 全书只 2~5 次换图许可。

    本 schema 把扩张做成**必填字段**，模型无法通过省略来逃避。
    """

    volume_no: int = Field(description="卷号，从 1 开始")
    title: str = Field(description="卷标题")
    goal: str = Field(description="本卷目标（人类可读）")
    # ── 升级台阶（必填）──
    new_locations: list[str] = Field(
        description="本卷新开地图（≥2 个，且不得与既往卷重复）"
    )
    new_factions: list[str] = Field(
        description="本卷新登场势力（≥2 个，且不得与既往卷重复）"
    )
    antagonist: str = Field(description="本卷主要对手（必须与既往卷不同）")
    power_ceiling: str = Field(description="本卷力量/能力上限")
    core_conflict: str = Field(description="本卷核心矛盾")
    escalation_from_prev: str = Field(
        min_length=8,
        description="相对上卷「升级在哪」——必须写具体内容，"
        "禁止「推向X的下一个阶段」这类模板句"
        "（模板句由 structure_gate 进一步拦截，此处只挡空值与过短）",
    )
    # ── 收尾 ──
    old_map_closure: str = Field(
        min_length=2,
        description="旧地图如何收尾（有交代地离开，而非弃置）",
    )
    volume_end_hook: str = Field(default="", description="卷末钩子")
    # ── 规模 ──
    chapter_count: int = Field(description="本卷章数（建议 150~250）")
    milestones: list[MilestoneOut] = Field(
        default_factory=list,
        description="卷内里程碑（间隔不超过 25 章）",
    )

    @field_validator("new_locations", "new_factions")
    @classmethod
    def _not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("必须至少给出 1 项（长篇需要横向扩张，见卷级台阶要求）")
        return v

    @model_validator(mode="before")
    @classmethod
    def _normalize_shapes(cls, data: Any) -> Any:
        """字段名漂移 + 的等价形状归一。

        实测：模型给出 `volume_number` 而非 `volume_no`（同义命名），
        并把 `milestones` 写成 `[8, 16]`（裸章号）。
        前者是**命名偏好**、后者是**等价简写**，都不该让整条链路失败。

        ⚠️ 只映射**明确同义**的字段名，不做模糊匹配 ——
        宽泛猜测会把真正的缺字段也"补"上，那比失败更危险。
        """
        if not isinstance(data, dict):
            return data
        d = apply_field_aliases(data)
        if "milestones" in d:
            ms = d["milestones"]
            # ⚠️ 只包装**数值**形状。
            #
            # 早先这里把 str 也一起包装成 `{"chapter_no": "第6章：…"}`，
            # 结果是**绕过了** `MilestoneOut` 处理字符串的校验器
            # （它拿到的是 dict 而不是 str），解析照样失败。
            # 教训：归一化层之间不能互相遮蔽 —— 能自己处理的形状就原样传递。
            if isinstance(ms, list) and ms and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in ms
            ):
                d["milestones"] = [{"chapter_no": int(x)} for x in ms]
        return d


class SeriesPlanOut(BaseModel):
    """全书规划 —— **必须一次给出全部卷的升级台阶**。

    ★ 这是与旧项目最本质的差异。旧项目 `scales.json` 的 ultra 档明文规定
    **「本次只细化第 1 卷」**，于是模型把全部章数分配给第 1 卷。
    """

    logline: str = Field(description="一句话故事梗概")
    theme: str = Field(default="", description="主题")
    tone: str = Field(default="", description="基调")
    total_volumes: int = Field(description="总卷数")
    target_chapters: int = Field(description="全书目标章数")
    volumes: list[VolumePlanOut] = Field(description="**全部卷**的升级台阶")

    @model_validator(mode="before")
    @classmethod
    def _normalize_field_names(cls, data: Any) -> Any:
        """字段名漂移归一（实测：`target_total_chapters`、`volume_count`）。"""
        return apply_field_aliases(data)

    @field_validator("volumes")
    @classmethod
    def _volumes_match(cls, v: list[VolumePlanOut], info) -> list[VolumePlanOut]:
        tv = info.data.get("total_volumes")
        if tv is not None and len(v) != tv:
            raise ValueError(
                f"声明 {tv} 卷但只规划了 {len(v)} 卷 —— "
                f"旧项目正是这样产生 blueprint=missing 的占位卷"
            )
        return v


# --------------------------------------------------------------------------- #
# 骨架（本卷弧线/段落/角色/势力）
# --------------------------------------------------------------------------- #


class ArcOut(BaseModel):
    key: str = Field(description="弧线标识（简短、唯一）")
    kind: str = Field(description="core（主线）| relationship（关系线）| opposition（对立线）| sub（支线）")
    description: str = Field(description="这条线讲什么")
    chapter_span: list[int] = Field(
        default_factory=list, description="[起始章, 结束章]（全书章号）"
    )


class SkeletonOut(BaseModel):
    """本卷骨架 —— 弧线 / 段落 / 角色 / 势力。"""

    arcs: list[ArcOut] = Field(description="故事线（≥6 条，其中核心线 ≥2、关系线 ≥2、对立线 ≥2）")
    segments: list[str] = Field(
        default_factory=list,
        description="本卷分段目标（每段一句，**必须两两不同**）",
    )
    characters: list[str] = Field(description="本卷主要角色名（≥3）")
    factions: list[str] = Field(description="本卷登场势力名（≥4）")


# --------------------------------------------------------------------------- #
# 章节细纲
# --------------------------------------------------------------------------- #


class SceneBeatOut(BaseModel):
    """一个场景拍点。

    继承旧项目**已验证有效**的格式（`flowmode/base.py`）：
    「场景/人物：目标→阻碍→结果」的分场景拍点表，双向硬约束
    （防遗漏 + 防加戏）。新增 `why_now` 要求动机由上一拍逼出。
    """

    goal: str = Field(description="谁想做什么（必须具体到动作与对象）")
    obstacle: str = Field(description="阻碍是什么")
    outcome: str = Field(description="结果是什么（必须可检验）")
    why_now: str = Field(
        description="为什么是现在 —— 必须由上一行的结果或上一章出口状态逼出，"
        "禁止无动机的地点跳转"
    )
    location: str = Field(default="", description="场景地点名（若有）")
    participants: list[str] = Field(default_factory=list, description="在场人物名")


class WorldStateOut(BaseModel):
    """章首/章末世界状态 —— **结构化**（旧项目是自由文本，填充率仅 13%）。"""

    location: str = Field(default="", description="地点名")
    story_time: str = Field(default="", description="故事内时间（如「第三日夜」）")
    present: list[str] = Field(default_factory=list, description="在场人物名")
    known: list[str] = Field(
        default_factory=list,
        description="本章结束时，**谁知道什么**（用于防止角色知道不该知道的事）",
    )
    open_thread: str = Field(
        default="",
        description="章末未收束的事件本身（谁在做什么、什么没解决）。"
        "禁止写「莫名的波动」这类无主暗示",
    )


class ChapterPlanOut(BaseModel):
    """一章细纲。"""

    title: str = Field(description="章标题（具体、有信息量，禁止与其他章重复）")
    plot: str = Field(description="一句话剧情梗概")
    beat_type: str = Field(
        default=BeatType.NORMAL.value,
        description=f"节拍类型：{_enum_values(BeatType)}",
    )
    is_transition: bool = Field(
        default=False,
        description="本章是否换场章（换地图/换时间）。"
        "**规划内的换场是正常的**，不是缺陷（旧项目把换图一律判为加戏）",
    )
    scenes: list[SceneBeatOut] = Field(description="分场景拍点（4~8 个）")
    entry_state: WorldStateOut = Field(description="本章开场状态")
    exit_state: WorldStateOut = Field(description="本章结尾状态（下一章据此开场）")
    end_hook: str = Field(description="章末钩子：本章现场内**悬而未决的事件本身**")

    @field_validator("scenes")
    @classmethod
    def _min_scenes(cls, v: list[SceneBeatOut]) -> list[SceneBeatOut]:
        if len(v) < 4:
            raise ValueError(f"场景数 {len(v)} < 4（细纲要求 4~8 个分场景拍点）")
        return v


class DetailBatchOut(BaseModel):
    """细纲批次输出。"""

    chapters: list[ChapterPlanOut] = Field(description="本批各章细纲")


# --------------------------------------------------------------------------- #
# 抽取（拆分后：每节点只抽一类）
# --------------------------------------------------------------------------- #


class EntityMentionOut(BaseModel):
    """正文中提到的实体（供 `entity_resolver` 归一）。"""

    name: str = Field(description="正文中出现的名字")
    kind: str = Field(
        description=f"实体类型：{_enum_values(EntityKind)}"
        "（**势力**与**组织**都是合法值）"
    )
    aliases: list[str] = Field(default_factory=list, description="同章出现的别名")
    intro_method: str = Field(
        default="unknown",
        description="引入方式：self_intro | mentioned_by_other | narrator_intro | "
        "appeared_before | baseless_recall | baseless_knowledge | "
        "appeared_out_of_nowhere | unknown",
    )
    context: str = Field(default="", description="首次出现的上下文句子（≤100 字）")


class StateUpdateOut(BaseModel):
    """单个实体的状态变化。"""

    entity: str = Field(description="实体名")
    field: str = Field(description="字段名（位置/境界/目标/情绪/装备/性格/立场 等）")
    value: str = Field(description="新值（字符串或 JSON）")
    evidence: str = Field(
        default="",
        description="正文中的依据（本章哪个事件导致了该变化）",
    )


class InjuryUpdateOut(BaseModel):
    """损伤变化。

    ★ 只报**有变化**的条目 —— 未变化绝不重复输出
    （旧项目规则「同值不覆盖、不推进计时，防慢性损伤无限续期」）。
    """

    entity: str = Field(description="受伤者名字")
    name: str = Field(description="损伤名（如「左肩剑创」）")
    kind: str = Field(description=f"类别：{_enum_values(InjuryKind)}")
    severity: str = Field(description=f"严重度：{_enum_values(InjurySeverity)}")
    status: str = Field(description=f"状态：{_enum_values(InjuryStatus)}")
    recov: str = Field(default="", description="恢复方向（新发/恶化时给）")
    evidence: str = Field(default="", description="正文依据")


class EventOut(BaseModel):
    """关键事件。

    ★ 动作（查看/记录/离开/拾取/值守）属于**事件**，不属于关系 ——
    旧项目把动作写进关系表，导致 966 条关系里 86.7% 是非人际关系。
    """

    story_time: str = Field(default="", description="故事内时间")
    location: str = Field(default="", description="地点名")
    participants: list[str] = Field(default_factory=list, description="参与者名字")
    description: str = Field(description="事件描述")
    consequence: str = Field(default="", description="后果/影响")


class RelationUpdateOut(BaseModel):
    """**社会关系**变化（只抽关系，不抽动作）。"""

    from_entity: str = Field(description="关系主体名")
    to_entity: str = Field(description="关系客体名")
    kind: str = Field(description=f"关系类型：{_enum_values(RelationKind)}")
    intensity: int = Field(default=50, ge=0, le=100, description="强度 0~100")
    sentiment: str = Field(
        default=RelationSentiment.NEUTRAL.value,
        description=f"情感倾向：{_enum_values(RelationSentiment)}",
    )
    surface_facing: str = Field(default="", description="世人所知的关系表现")
    true_facing: str = Field(default="", description="真实立场（与表面不同即有暗线张力）")
    is_mutual: bool = Field(default=True, description="是否双向关系")
    evidence: str = Field(default="", description="正文依据")


class SceneExtractionOut(BaseModel):
    """`scene_extractor` 的输出：事件 + 状态 + 损伤。"""

    summary: str = Field(description="本章梗概（80 字内）")
    events: list[EventOut] = Field(default_factory=list, description="2~5 条关键事件")
    state_updates: list[StateUpdateOut] = Field(default_factory=list)
    injury_updates: list[InjuryUpdateOut] = Field(default_factory=list)
    deaths: list[str] = Field(default_factory=list, description="本章死亡的角色名")
    entities: list[EntityMentionOut] = Field(
        default_factory=list, description="本章出现的实体（供实体归一）"
    )
    unsupported: list[str] = Field(
        default_factory=list,
        description="无引入依据的名词（凭空出现/无依据回忆/无依据知晓）",
    )


class EntityResolutionOut(BaseModel):
    """`entity_resolver` 的输出：提及 → 既有实体 或 新建。"""

    mentions: list[str] = Field(description="本章出现的实体名")
    links: dict[str, int] = Field(
        default_factory=dict,
        description="提及名 → 既有实体 ID（能对上时给出）",
    )
    new_canonical: dict[str, str] = Field(
        default_factory=dict,
        description="提及名 → 规范名（新建实体或归一到既有正名时给出）",
    )


class RelationExtractionOut(BaseModel):
    """`relation_extractor` 的输出：只抽社会关系。"""

    relations: list[RelationUpdateOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 审校
# --------------------------------------------------------------------------- #


class ReviewIssueOut(BaseModel):
    severity: str = Field(description="critical | major | minor")
    category: str = Field(
        description="问题类别：ai_style | consistency | pacing | logic | "
        "foreshadow | ooc | outline_deviation | other"
    )
    location: str = Field(default="", description="问题位置（章内定位，如「末段」）")
    description: str = Field(description="问题说明（具体、可执行）")
    suggestion: str = Field(default="", description="修改建议")


class ReviewResultOut(BaseModel):
    """LLM 审校结果。

    ## ★ 关键约束：`issues` 非空则 `revised_content` 必须非空

    旧项目实测：Editor 反复要求修改但**从不产出可用的 `revised_content`**
    （`no_revised_content`、`too_short:3839<4600`、`no_improve:6->6`）
    → 重写环节空转 → 原稿入库。

    且旧项目模型曾在 `notes` 里声称"已改写"而 `revised_content=null`
    （`AGENTS.md` 记录）。因此：
      · 本 schema 用 **model_validator** 强制两者一致
        （⚠️ 不能用 field_validator —— Pydantic 按声明顺序校验字段，
          `revised_content` 声明在 `issues` 之前，那时 `info.data` 里还没有 issues，
          校验会静默失效。这个坑实测踩过一次。）
      · `actions_taken` **由代码**依据 `revised_content` 生成，不采信模型自述
    """

    issues: list[ReviewIssueOut] = Field(default_factory=list, description="发现的问题")
    revised_content: str | None = Field(
        default=None,
        description="**修正后的完整正文**（若 issues 非空则必须给出全文，"
        "不得只给片段或说明）",
    )
    score: float = Field(ge=0, le=10, description="质量评分（0~10）")
    notes: str = Field(default="", description="补充说明（散文，**不作为判据**）")

    @model_validator(mode="after")
    def _revised_required_when_issues(self) -> ReviewResultOut:
        if self.issues and not (self.revised_content or "").strip():
            raise ValueError(
                "issues 非空时必须给出 revised_content（完整修正稿）。"
                "旧项目实测：Editor 反复指出问题却不给修订稿 → 重写空转 → 原稿入库"
            )
        return self

    @property
    def has_usable_revision(self) -> bool:
        """是否有可用的修订稿（供代码侧判定，不采信模型自述）。"""
        return bool((self.revised_content or "").strip())


# --------------------------------------------------------------------------- #
# 伤势节拍（由调度器产出，不调 LLM；此处仅为契约完整性）
# --------------------------------------------------------------------------- #


class HealthBeatOut(BaseModel):
    """伤势节拍（`HealthScheduler` 产出）。"""

    injury_name: str = Field(description="损伤名")
    entity: str = Field(description="受伤者")
    action: str = Field(description=f"动作：{_enum_values(HealthAction)}")
    chapter_no: int = Field(description="全书章号")
    scene_seq: int = Field(default=0, description="指向本章第几个场景（0-based）")
    note: str = Field(default="")


__all__ = [
    "BibleOut",
    "MilestoneOut",
    "VolumePlanOut",
    "SeriesPlanOut",
    "ArcOut",
    "SkeletonOut",
    "SceneBeatOut",
    "WorldStateOut",
    "ChapterPlanOut",
    "DetailBatchOut",
    "EntityMentionOut",
    "StateUpdateOut",
    "InjuryUpdateOut",
    "EventOut",
    "RelationUpdateOut",
    "SceneExtractionOut",
    "EntityResolutionOut",
    "RelationExtractionOut",
    "ReviewIssueOut",
    "ReviewResultOut",
    "HealthBeatOut",
    "LifecycleState",
]
