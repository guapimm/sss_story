"""Prompt 库 —— **单一来源**，且按缓存分层组织（对策⑥ + 对策⑦）。

## 两条设计原则

### 1. 单一来源

旧项目 prompt 散落在 6 个模块的 docstring 与内联字符串里
（`architect.py` 17,372 字符 / 32 块、`pipeline.py` 16,904 / 44 块），
且 6 个模块把 2.4k~3.4k 字符的设计文档写在 docstring 里，
与 prompt 文本同层级 —— 定位困难。

本模块把 prompt 集中在 `infra/prompts/`（Markdown 文件），代码只负责**组装**。

### 2. 按缓存分层组织

`PromptLayers` 三层（见 `infra/llm/prompt_builder.py`）：

    ① 稳定前缀  全书不变：角色定义 + 输出约束 + 世界观圣经
    ② 准稳定层  按卷/段变：卷级台阶 + 段目标 + 文风规则
    ③ 易变尾部  每章变：状态表 / 时间线 / 前文 / 本章细纲 / **字数参数**

**实测依据**（旧项目 writer prompt 逐块预算分解）：
  · 单章 prompt 30,115 字符（≈10.7k–13.3k token）
  · 稳定 11,998（36.5%）/ 易变 19,117（63.5%）
  · 真正跨请求完全不变的只有 **5,209 字符（17%）**
  · **拼装顺序是缓存杀手**：每章变的「文风范本」（~800 字符）插在
    「世界观圣经」（6,000 字符，卷内不变）**之后**，把后面 19k 全部推出缓存前缀
  · 另一处单点最大破坏：`bible_inject.select` 用 `f"{plot} {title}"` 做 query
    —— **逐章不同** → 全书最稳的圣经前缀**在第一个块就分叉**

**三条禁令**（由 `PromptLayers.validate_stability` 与契约测试强制）：
  · 字数参数**不得**注入稳定层（旧项目 `.format(MIN_CHARS=...)` 让 system 逐章变化）
  · 时间戳 / 随机数 / 章号**不得**出现在稳定层
  · 稳定层与易变层之间**不得夹任何易变内容**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from storyforge.config import REPO_ROOT
from storyforge.domain.enums import (
    BeatType,
    EntityKind,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
    RelationKind,
    RelationSentiment,
)
from storyforge.infra.llm.prompt_builder import PromptLayers

#: prompt 模板目录
PROMPTS_DIR = REPO_ROOT / "src" / "storyforge" / "infra" / "prompts"


def _load(name: str) -> str:
    """加载 prompt 模板。缺失**抛错**（不静默用空串）。"""
    p = PROMPTS_DIR / f"{name}.md"
    if not p.exists():
        raise FileNotFoundError(
            f"prompt 模板缺失：{p}（prompt 必须集中管理，见本模块 docstring）"
        )
    return p.read_text(encoding="utf-8").strip()


def _enum_list(enum_cls) -> str:
    return " | ".join(e.value for e in enum_cls)


# --------------------------------------------------------------------------- #
# 稳定前缀（全书不变）
# --------------------------------------------------------------------------- #


#: 世界观圣经的注入前缀。**注意**：这里只放圣经文本本身，
#: 不放任何逐章变化的内容（旧项目正是在这里插了按章裁剪的 query）。
BIBLE_HEADER = "【世界观圣经】"


def bible_block(bible_text: str) -> str:
    """圣经块 —— 稳定前缀的第二部分（卷内不变）。

    旧项目 `bible_inject.select` 按章裁剪，导致全书最稳的前缀在第一个块就分叉。
    本设计：**圣经全文进稳定前缀**，若确需裁剪，只裁剪「与本卷相关」的块，
    且裁剪依据**按卷**（不是按章）—— 这样卷内稳定。
    """
    return f"{BIBLE_HEADER}\n{bible_text.strip()}"


# --------------------------------------------------------------------------- #
# 准稳定层（按卷/段变化）
# --------------------------------------------------------------------------- #


def volume_block(volume_no: int, title: str, goal: str, escalation: str = "") -> str:
    """卷级台阶块 —— 同卷内不变（准稳定层）。"""
    parts = [f"【本卷】第{volume_no}卷《{title}》", f"卷目标：{goal}"]
    if escalation:
        parts.append(f"本卷升级：{escalation}")
    return "\n".join(parts)


def segment_block(segment_title: str, segment_goal: str) -> str:
    """段落目标块 —— 同段内不变（准稳定层）。"""
    return f"【本段】{segment_title}\n段落目标：{segment_goal}"


# --------------------------------------------------------------------------- #
# 易变尾部（每章变化）
# --------------------------------------------------------------------------- #


@dataclass
class ChapterContext:
    """一章的写作上下文（全部是易变内容，进 prompt 尾部）。"""

    global_no: int
    volume_no: int
    title: str
    plot: str
    #: 细纲的场景拍点（已渲染为文本）
    plot_detail: str = ""
    end_hook: str = ""
    #: 前后锚点（继承旧项目已验证有效的机制）
    prev_exit_state: str = ""
    next_entry_state: str = ""
    next_title: str = ""
    #: 状态与记忆
    states: str = ""
    relations: str = ""
    injuries: str = ""
    foreshadow_plant: list[str] = None  # type: ignore[assignment]
    foreshadow_resolve: list[str] = None  # type: ignore[assignment]
    timeline: str = ""
    rag_snippets: str = ""
    previous_tail: str = ""
    #: 伤势节拍指令（由 HealthScheduler 注入）
    health_beats: str = ""
    #: 节拍类型
    beat_type: str = BeatType.NORMAL.value
    #: 是否换场章
    is_transition: bool = False
    #: 文风规则（**准稳定层**内容，由 `build_chapter_layers` 放进 semi；
    #: 此处仅为传递，`ChapterContext` 本身整体进易变层）
    style_rules: str = ""

    def __post_init__(self) -> None:
        if self.foreshadow_plant is None:
            self.foreshadow_plant = []
        if self.foreshadow_resolve is None:
            self.foreshadow_resolve = []


def detail_block(ctx: ChapterContext) -> str:
    """细纲块 —— 场景骨架硬约束。

    继承旧项目**已验证有效**的设计（`flowmode/base.py:148-199`）：
    细纲是分场景拍点表，**双向硬约束**（防遗漏 + 防加戏）。

    ★ 但有一条**关键修正**：旧项目在这里写「禁止新增细纲之外的重要人物、
    重大转折、新功法/新设定/**新地图**」，配合 `editor.py:106` 把新地图记为
    「细纲偏离」→ **换图在写作层与审校层被双重禁止**，
    加上 8 处同类禁令，导致全书只 2~5 次换图许可
    → 400 章困在一栋楼里（实测：书 23 场景 token 主体是「门/电梯/楼层/走廊」）。

    本设计改为：**规划内的换场正常，规划外的才是加戏**。
    """
    parts: list[str] = []
    if ctx.plot_detail:
        parts.append(f"【本章细纲——场景骨架硬约束】\n{ctx.plot_detail}\n")
        rules = [
            "细纲执行规则（最高优先级，逐行核对）：",
            "- 正文必须按细纲场景顺序展开，每个场景的「结果」与关键转折必须落实，"
            "不得遗漏、不得调换因果；",
            "- 场景内的对话措辞、动作笔触、感官细节与过场过渡可自由发挥："
            "发挥细节≠偏离大纲；",
            "- 细纲行与正文段落无需一一对应（可合并或拆分场景），但事件链必须完整。",
        ]
        if ctx.is_transition:
            # ★ 换场章：解除旧项目的换图禁令
            rules.append(
                "- 本章是**规划内的换场章**：可以换地图/换时间。"
                "换场须用一两句自然交代（赶路/整备/时间推进），"
                "不得硬切，也不必强行承接上一章的地点。"
            )
        else:
            rules.append(
                "- 禁止新增细纲之外的重要人物、重大转折、新功法/新设定——"
                "它们属于剧情级加戏。"
                "（**新地图**不在此列：若本章是换场章或细纲已规划，正常写）"
            )
        parts.append("\n".join(rules))
    if ctx.end_hook:
        parts.append(
            f"【章末钩子·硬性指令】本章只允许按规划钩子收尾：{ctx.end_hook}。"
            f"禁止自创无主暗示（如莫名的灵光/阴影/波动/人影悄然出现又消失），"
            f"除非该元素恰是上方 end_hook 的规划内容。"
        )
    return "\n\n".join(parts)


def anchor_block(ctx: ChapterContext) -> str:
    """前后锚点块 —— 让 writer「看到要去哪里」。

    旧项目实测（`docs/TODO_20260913.md` §7.1）：章间衔接断裂是最严重的质量缺陷 ——
    书 1005 第 1 章章末钩子落空，第 2 章从「拒绝给李巡长开门」直接跳到
    「在镇公所与李巡长同席」，中间没有动机拍。

    **根因不是细纲长度，而是 writer 只知道自己这一章**。
    把上一章出口状态 + 下一章入口状态显式给出，章末才有必须抵达的位置。
    """
    window: list[str] = []
    if ctx.prev_exit_state:
        window.append(
            f"- 上一章出口状态（本章开场必须与之完全一致，"
            f"不得跳过复述或悄然改时间/地点/在场人物）：{ctx.prev_exit_state}"
        )
    if ctx.next_entry_state:
        head = f"下一章《{ctx.next_title}》" if ctx.next_title else "下一章"
        window.append(
            f"- {head}的入口状态：{ctx.next_entry_state}\n"
            f"  本章必须把剧情推进到「该状态可自然承接」的位置："
            f"章末落点要与它因果相连，上一拍的结果要能逼出下一拍的开场动机。"
            f"**不得写出下一章的内容**，也不得在章末另起与它无关的悬念。"
        )
    if not window:
        return ""
    return (
        "【前后锚点·章间衔接硬约束】相邻章必须因果相接，禁止地点/状态跳变：\n"
        + "\n".join(window)
        + "\n- 若本章中途要换地点或改变人物关系状态，必须写出可叙述的过渡与动机"
        "（为什么现在去、凭什么见得着、上一拍的结果如何逼出这一拍），"
        "不得从「拒绝见某人」直接跳到「与某人同席交谈」。"
    )


def health_block(ctx: ChapterContext) -> str:
    """伤势节拍块（对策④）。

    ★ 与旧项目的**关键差异**：旧项目把「⚠️已超恢复期，本轮必须推进」
    注入到 **writer**，而 writer 必须服从细纲的「最高优先级」→
    **实测 writer 服从细纲**（这是正确的服从）→ 恢复永不发生（**恢复率 0%**）。

    本设计：伤势推进指令**只经细纲下达**（`health_beats` 已由
    `HealthScheduler` 排进场景），writer 收到的是**有场景支撑的指令**，
    不再面对矛盾。
    """
    if not ctx.health_beats:
        return ""
    return (
        "【伤势推进·本章已排入细纲】\n"
        f"{ctx.health_beats}\n"
        "（以上伤势节拍已占用本章真实场景，按细纲正常写出即可，"
        "不要额外加疗伤桥段）"
    )


def foreshadow_block(ctx: ChapterContext) -> str:
    parts: list[str] = []
    if ctx.foreshadow_plant:
        parts.append("【伏笔任务·本章需埋下】\n- " + "\n- ".join(ctx.foreshadow_plant))
    if ctx.foreshadow_resolve:
        parts.append("【伏笔任务·本章需回收】\n- " + "\n- ".join(ctx.foreshadow_resolve))
    return "\n\n".join(parts)


def memory_block(ctx: ChapterContext) -> str:
    """状态/关系/时间线/RAG 块（易变尾部）。"""
    parts: list[str] = []
    if ctx.states:
        parts.append(f"【人物状态表·写作时须遵守】\n{ctx.states}")
    if ctx.relations:
        parts.append(f"【关系网络·出场实体的既有关系，不得违背】\n{ctx.relations}")
    if ctx.injuries:
        parts.append(f"【损伤状态·行为须一致】\n{ctx.injuries}")
    if ctx.timeline:
        parts.append(f"【前情时间线】\n{ctx.timeline}")
    if ctx.previous_tail:
        parts.append(
            "【上一章结尾原文——本章开篇必须无缝承接此段的场景、语气与情绪】\n"
            f"…{ctx.previous_tail}"
        )
    if ctx.rag_snippets:
        parts.append(f"【相关前文片段】\n{ctx.rag_snippets}")
    return "\n\n".join(parts)


def beat_block(beat_type: str) -> str:
    """节拍专属写作指令。"""
    table = {
        BeatType.CLIMAX.value: (
            "【💥 本章为爆点章】信息密度拉满：多条伏线在此交汇引爆，"
            "揭示必须有前文铺垫支撑，冲击力通过具体后果呈现而非感叹。"
        ),
        BeatType.BOSS.value: (
            "【⚔️ 本章为 Boss 战/大对决】核心冲突的高潮呈现："
            "局势至少一次颠覆，胜利须付出可见代价。"
        ),
        BeatType.EMOTION.value: (
            "【💧 本章为情绪点——事件驱动】情绪必须由此前真实事件积累触发"
            "（正文中自然回扣该事件细节），禁止无铺垫煽情、禁止直接命名情绪；"
            "以动作、沉默、生理反应呈现。"
        ),
        BeatType.RECALL.value: (
            "【🕰️ 本章含回忆杀】回忆内容必须呼应时间线中的具体旧事件"
            "（人名/地点/物件与前文一致），回忆与现实交织推进，禁止泛泛闪回。"
        ),
        BeatType.TURNING.value: (
            "【🔄 本章为转折章】局面发生根本改变，转折须有前文伏笔支撑并当场兑现因果。"
        ),
        BeatType.HEAL.value: (
            "【🩹 本章为恢复章】按细纲的伤势节拍写痊愈/解除过程："
            "必须有可见的治疗动作与代价，禁止一句话带过。"
        ),
        BeatType.RECOVERY_PREP.value: (
            "【🩹 本章含恢复铺垫】寻医/求药/疗伤尝试——"
            "为后续痊愈做铺垫，不必在本章痊愈。"
        ),
        BeatType.TRANSITION.value: (
            "【🧭 本章为换场章】可换地图/换时间。用一两句交代间隔后进入新场景，"
            "禁止时间硬切词（次日/三日后/与此同时），"
            "也禁止为制造钩子引入细纲之外的新悬念。"
        ),
    }
    return table.get(beat_type, "")


def length_block(min_chars: int, soft_max: int) -> str:
    """字数块 —— **必须放易变尾部**。

    ⚠️ 旧项目把 `MIN_CHARS`/`SOFT_MAX` 通过 `.format()` 注入 **system prompt**，
    导致 system 逐章变化 → **缓存前缀逐章失效**。
    本设计把它放在 user 尾部（recency 优先 + 不污染稳定层）。
    """
    return (
        f"【输出要求】直接输出本章完整正文本身：不要 JSON、不要代码块包裹、"
        f"不要「正文：」等前缀，不要标题。\n"
        f"字数目标 {min_chars}~{soft_max} 字；{soft_max} 为硬性上限，"
        f"严禁超过（超过即视为注水）。禁止注水凑字。"
    )


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #


def build_chapter_layers(
    *,
    bible_text: str,
    volume_block_text: str,
    segment_block_text: str,
    ctx: ChapterContext,
    min_chars: int,
    soft_max: int,
    style_rules: str = "",
) -> PromptLayers:
    """组装一章的三层 prompt。

    层次归属（**这是缓存命中的关键**）：
      · `stable`  = system 角色定义 + 输出格式约束 + 世界观圣经
      · `semi`    = 本卷台阶 + 本段目标 + 文风规则（固定文本）
      · `volatile`= 状态/关系/损伤/时间线/RAG/前文/锚点/细纲/节拍/钩子/**字数**
    """
    layers = PromptLayers()
    # ① 稳定前缀
    layers.stable.add("system", _load("writer_system"))
    layers.stable.add("bible", bible_block(bible_text))
    # ② 准稳定层
    layers.semi.add("volume", volume_block_text)
    if segment_block_text:
        layers.semi.add("segment", segment_block_text)
    if style_rules:
        layers.semi.add("style_rules", style_rules)
    # ③ 易变尾部
    layers.volatile.add("memory", memory_block(ctx))
    layers.volatile.add("anchor", anchor_block(ctx))
    layers.volatile.add("foreshadow", foreshadow_block(ctx))
    layers.volatile.add("health", health_block(ctx))
    layers.volatile.add("beat", beat_block(ctx.beat_type))
    layers.volatile.add("detail", detail_block(ctx))
    layers.volatile.add("length", length_block(min_chars, soft_max))
    return layers


def build_extractor_layers(*, instructions: str, prose: str, known: str = "") -> PromptLayers:
    """组装抽取节点的 prompt。

    ★ 抽取类节点的 prompt **不含世界观圣经** —— 抽取只需本章正文与既有账本，
    塞入圣经只会增加成本且稀释注意力（旧项目 `post_processor` 的
    system prompt 超过 10,000 字符，一次抽 11 类信息 → `entity_classify` 空输出 **100%**）。
    """
    layers = PromptLayers()
    layers.stable.add("system", instructions)
    if known:
        layers.volatile.add("known", known)
    layers.volatile.add("prose", prose)
    return layers


__all__ = [
    "PROMPTS_DIR",
    "BIBLE_HEADER",
    "ChapterContext",
    "bible_block",
    "volume_block",
    "segment_block",
    "detail_block",
    "anchor_block",
    "health_block",
    "foreshadow_block",
    "memory_block",
    "beat_block",
    "length_block",
    "build_chapter_layers",
    "build_extractor_layers",
    "EntityKind",
    "InjuryKind",
    "InjurySeverity",
    "InjuryStatus",
    "RelationKind",
    "RelationSentiment",
]
