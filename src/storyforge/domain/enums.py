"""领域枚举 —— **所有枚举的唯一来源**。

## 为什么这个模块很关键

旧项目有**多处枚举定义互相冲突**，每个冲突都造成过实际数据损坏：

| 冲突 | 后果 | 证据 |
|---|---|---|
| 「势力」在 `entities.py` 不存在、在 `models.py` 存在、`graph.py` 两者都无 | LLM 输出的 `type="势力"` 被判非法 → **静默降级「其他」** → 势力实体 **0 个** | doc/research/01 §12bis.4 |
| severity 阈值表 5 档（轻微/轻/中/重/极重），而 prompt 只允许 3 档（轻/重/极重） | 「轻微」「中」**永远不会被产生**，而这两档是最快催恢复的 → 注意力反向引导 | `memory.py:291-294` 自述 |
| `relation_type` 是**自由文本** | 254 条人物-人物边用了 **191 种**标签，9 词受控词表只覆盖 **12.2%** → 无法聚合/比较 | 本次独立复核 |

**因此**：本模块是唯一来源；prompt 约束由这些枚举**生成**（见 infra/prompts），
DB 列存枚举的 `.value`，任何新增枚举值必须同时更新 prompt 生成器与测试。

## 命名约定

成员的 Python 名用英文，`.value` 用中文（与旧项目数据/用户界面一致，
便于未来与旧库做只读对照）。
"""

from __future__ import annotations

from enum import StrEnum


class BookMode(StrEnum):
    """书籍模式。一期只实现 STORY（决策 D-08），LEARN 保留接口不实现。"""

    STORY = "story"
    LEARN = "learn"


class BookStatus(StrEnum):
    """书籍状态。

    旧项目 `Book.phase` 是**无约束 TEXT 列**、43 处散落赋值、15 个状态，
    且实测 `architecting` **不在** `ACTIVE_PHASES`（`pipeline.py:535` vs `:2009`）
    → 架构师阶段重启后 `recover_orphans` **不认它**，书**永久卡死**。

    本设计：枚举 + 单一转换函数（domain/state_machine.py），
    并在测试中断言 `ACTIVE_STATUSES` 覆盖所有"进行中"状态。
    """

    CREATED = "created"
    BIBLE_RUNNING = "bible_running"
    #: 等待人工确认（C-1 圣经 / C-4 全卷台阶）
    AWAITING_CONFIRM = "awaiting_confirm"
    PLANNING = "planning"
    WRITING = "writing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    #: 进程被杀后由恢复流程标记
    INTERRUPTED = "interrupted"
    #: 条件触发的人工确认（C-6 核心实体不可逆消失 / C-7 质量闸门连续失败）
    NEEDS_HUMAN = "needs_human"


#: 「进行中」状态集合 —— ⚠️ 必须覆盖所有运行态。
#: 旧项目正因 `architecting` 漏在此集合外，导致书永久卡死。
#: tests/unit/test_state_machine.py 会断言此集合与 BookStatus 的完整性。
ACTIVE_STATUSES: frozenset[BookStatus] = frozenset(
    {
        BookStatus.BIBLE_RUNNING,
        BookStatus.PLANNING,
        BookStatus.WRITING,
    }
)

#: 终态
TERMINAL_STATUSES: frozenset[BookStatus] = frozenset(
    {BookStatus.COMPLETED, BookStatus.FAILED}
)


class EntityKind(StrEnum):
    """实体类型 —— **单一来源**。

    ★ 关键修正：`ORGANIZATION` 与 `FACTION` 在此**合并为一个概念**（势力）。
    旧项目三套词汇表冲突导致势力实体 0 个（详见模块 docstring）。
    这里保留两个枚举名（便于语义区分：FACTION=势力/宗门，ORGANIZATION=组织/机构），
    但**归一到同一套归一化函数**（见 normalize_entity_kind）。
    """

    CHARACTER = "人物"
    FACTION = "势力"
    LOCATION = "地点"
    ITEM = "物品"
    TECHNIQUE = "功法"
    RULE = "规则"
    TERM = "术语"
    ORGANIZATION = "组织"
    EVENT = "事件"
    OTHER = "其他"


#: 归一化别名表：把旧项目/模型可能输出的各种写法映射到规范值。
#: 这是消除「词汇表冲突」的关键 —— 不再有"判定不在枚举内就降级"的静默路径。
_ENTITY_KIND_ALIASES: dict[str, EntityKind] = {
    "势力": EntityKind.FACTION,
    "组织": EntityKind.ORGANIZATION,
    "宗门": EntityKind.FACTION,
    "家族": EntityKind.FACTION,
    "门派": EntityKind.FACTION,
    "国家": EntityKind.FACTION,
    "团体": EntityKind.ORGANIZATION,
    "机构": EntityKind.ORGANIZATION,
    "地名": EntityKind.LOCATION,
    "地点": EntityKind.LOCATION,
    "场所": EntityKind.LOCATION,
    "人物": EntityKind.CHARACTER,
    "角色": EntityKind.CHARACTER,
    "人名": EntityKind.CHARACTER,
    "物品": EntityKind.ITEM,
    "道具": EntityKind.ITEM,
    "装备": EntityKind.ITEM,
    "器物": EntityKind.ITEM,
    "功法": EntityKind.TECHNIQUE,
    "武学": EntityKind.TECHNIQUE,
    "术法": EntityKind.TECHNIQUE,
    "规则": EntityKind.RULE,
    "条文": EntityKind.RULE,
    "术语": EntityKind.TERM,
    "概念": EntityKind.TERM,
}


def normalize_entity_kind(raw: str) -> EntityKind:
    """把任意写法归一为 `EntityKind`。

    **未知类型抛错而不是静默降级为 OTHER** —— 旧项目正是"判定不在枚举内 →
    降级为「其他」"这条静默路径，使 `type="势力"` 的 966 条关系里只剩 7 条。
    调用方（抽取节点）应捕获此异常并触发重试或记录问题，而不是接受降级。
    """
    if not raw:
        raise ValueError("实体类型为空")
    key = raw.strip()
    try:
        return EntityKind(key)
    except ValueError:
        pass
    if key in _ENTITY_KIND_ALIASES:
        return _ENTITY_KIND_ALIASES[key]
    raise ValueError(
        f"未登记的实体类型 {raw!r}。请加入 EntityKind 或 _ENTITY_KIND_ALIASES "
        f"（禁止静默降级为 OTHER —— 旧项目因此丢失了全部势力实体）"
    )


class LifecycleState(StrEnum):
    """实体生命周期 —— 替代旧项目的**名字前缀**方案。

    旧项目把状态塞进名字（`[毁·可修]青锋剑`），需要专门的 `_strip_life_prefix`，
    且剥离**顺序敏感**：`memory.py:668-669` 实证 ——
    `+[失·不可寻]家庭合影` 若先剥前缀，`[失·不可寻]` 会残留并逃过归一化（误杀）。

    另外旧项目 `[耗·一次性]` **既不在 `_PERMANENT_STATES` 也不在
    `_RECOVERABLE_STATES`**（`memory.py:216-220`），完全失管 —— 消耗品用尽后
    既不禁止使用也不提示恢复。本设计的 `CONSUMED` 有明确归类。
    """

    ACTIVE = "active"
    #: [毁·可修]
    DESTROYED_REPAIRABLE = "destroyed_repairable"
    #: [毁·不可逆]
    DESTROYED_PERMANENT = "destroyed_permanent"
    #: [失·可寻]
    LOST_RECOVERABLE = "lost_recoverable"
    #: [失·不可寻]
    LOST_PERMANENT = "lost_permanent"
    #: [耗·一次性] —— 旧项目完全失管的那一个
    CONSUMED = "consumed"
    #: [衰]
    DECLINING = "declining"
    #: [封]
    SEALED = "sealed"
    #: [转]
    TRANSFERRED = "transferred"
    #: [灭]
    ANNIHILATED = "annihilated"


#: 永久失去 —— 仅可通过回忆/遗物/旁引使用，禁止当作仍在持有
PERMANENT_LOSS_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.DESTROYED_PERMANENT,
        LifecycleState.LOST_PERMANENT,
        LifecycleState.CONSUMED,
        LifecycleState.ANNIHILATED,
    }
)

#: 可恢复 —— 恢复需**明确剧情过程**，禁止凭空恢复
RECOVERABLE_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.DESTROYED_REPAIRABLE,
        LifecycleState.LOST_RECOVERABLE,
        LifecycleState.DECLINING,
        LifecycleState.SEALED,
        LifecycleState.TRANSFERRED,
    }
)

#: ⚠️ 断言：每个 LifecycleState 必须落在永久失去、可恢复、或 active 三者之一。
#: 旧项目 `CONSUMED` 两边都不在 —— 这个断言就是为了防止那类遗漏。
assert PERMANENT_LOSS_STATES | RECOVERABLE_STATES | {LifecycleState.ACTIVE} == set(
    LifecycleState
), "有 LifecycleState 未归类（旧项目 [耗·一次性] 失管的教训）"


class InjuryKind(StrEnum):
    """损伤类别。"""

    WOUND = "伤势"
    SEQUELA = "后遗症"
    INNER_DEMON = "心魔"
    COST = "代价"
    CURSE = "诅咒"


class InjurySeverity(StrEnum):
    """损伤严重度 —— **5 档，单一来源**。

    ⚠️ 旧项目这里是最严重的枚举冲突之一：
      · `memory.py` 阈值表登记 **5 档**（轻微/轻/中/重/极重）
      · `post_processor.py` 的 prompt 只允许模型输出 **3 档**（轻/重/极重）
      · 未知 severity 走 fallback 并 `log_problem`

    → 「轻微」「中」**永远不会被产生**，而这两档（1 章、2 章）是最快催恢复的阈值。
    → 后果：`_impact_threshold` 拿不到 base → 返回 None → 该损伤**永不进入催促流程**。
    → 实测书 24：最重的「神魂受创（伤势·极重）」**恰恰是唯一不催的那条**，
      而轻伤催得最凶 —— **注意力被反向引导**（`memory.py:291-294` 自述）。

    本设计的防呆：`tests/unit/test_injury.py` 遍历 `InjurySeverity × InjuryKind`
    **全组合**断言每个都有阈值；prompt 约束由本枚举**生成**。
    """

    MINOR = "轻微"
    LIGHT = "轻"
    MODERATE = "中"
    SEVERE = "重"
    CRITICAL = "极重"


class InjuryStatus(StrEnum):
    """损伤状态机。"""

    NEW = "新发"
    WORSENED = "恶化"
    STABLE = "维持"
    IMPROVING = "好转"
    RELIEVED = "缓解"
    HEALED = "痊愈"
    REMOVED = "解除"
    PERMANENT_MITIGABLE = "永久·可缓解"
    BOUND = "绑定"


#: 恢复类状态（从这些状态可判定伤势推进）
RECOVERY_STATUSES: frozenset[InjuryStatus] = frozenset(
    {
        InjuryStatus.IMPROVING,
        InjuryStatus.RELIEVED,
        InjuryStatus.HEALED,
        InjuryStatus.REMOVED,
    }
)


class RelationKind(StrEnum):
    """社会关系类型 —— **枚举化**（旧项目是自由文本）。

    实测证据：254 条人物-人物边用了 **191 种**不同 `relation_type`，
    项目自带的 9 词受控词表只覆盖 **31/254 = 12.2%**；
    高频值是模型即兴造的标签（`问询者与被问者`6 / `试探与被试探`3 / `监视`3 …），
    平均每种只用 **1.33 次** → 系统**无法聚合、比较、要求"关系多样性"**。

    只保留**有叙事意义、会演化**的社会关系。归属走 `EntityAffiliation`，
    动作走 `Event`，物品持有走 `EntityState`。
    """

    # 人物 — 人物
    MASTER_APPRENTICE = "师徒"
    SWORN = "结拜"
    BLOOD = "血亲"
    COUPLE = "道侣"
    ALLY = "盟友"
    RIVAL = "对手"
    ENEMY = "敌对"
    NEMESIS = "仇敌"
    SUBORDINATE = "上下级"
    # 人物 — 势力
    #: 同门由「归属同一势力」+ 该关系表达（比裸"同门"更可聚合）
    SAME_SECT = "同门"
    MEMBER = "归属"
    LOYAL = "效忠"
    DEFECTED = "叛变"
    WANTED_BY = "通缉"
    # 势力 — 势力
    ALLIANCE = "联盟"
    VASSAL = "附属"
    FEUD = "世仇"
    # 通用
    DEBT = "债务"
    OATH = "立誓"
    CUSTOM = "其他关系"


class RelationSentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    #: 矛盾（既依赖又敌视这类张力）
    CONFLICTED = "conflicted"


#: 关系类型别名 → `RelationKind`。
#:
#: ★ 与 `_ENTITY_KIND_ALIASES` 同因：LLM 会给同义异形标签。
#: 旧项目 254 条人物边用了 **191 种**自由文本（受控词表只覆盖 12.2%），
#: `敌对/对手/仇视/对立` 无法归一 → 系统无法聚合、无法要求"关系多样性"。
#: 这里把**高频同义写法**收敛到枚举，是痛点 4 的直接对策。
_RELATION_KIND_ALIASES: dict[str, RelationKind] = {
    # 敌意族（旧项目实测最高频的混用）
    "仇视": RelationKind.NEMESIS,
    "仇恨": RelationKind.NEMESIS,
    "宿敌": RelationKind.NEMESIS,
    "死敌": RelationKind.NEMESIS,
    "对立": RelationKind.ENEMY,
    "敌视": RelationKind.ENEMY,
    "敌对关系": RelationKind.ENEMY,
    "竞争": RelationKind.RIVAL,
    "竞争关系": RelationKind.RIVAL,
    "宿敌关系": RelationKind.NEMESIS,
    # 亲善族
    "朋友": RelationKind.ALLY,
    "好友": RelationKind.ALLY,
    "同伴": RelationKind.ALLY,
    "合作": RelationKind.ALLY,
    "搭档": RelationKind.ALLY,
    "师父": RelationKind.MASTER_APPRENTICE,
    "弟子": RelationKind.MASTER_APPRENTICE,
    "师徒关系": RelationKind.MASTER_APPRENTICE,
    "兄弟": RelationKind.SWORN,
    "结义": RelationKind.SWORN,
    "亲人": RelationKind.BLOOD,
    "亲属": RelationKind.BLOOD,
    "父子": RelationKind.BLOOD,
    "母女": RelationKind.BLOOD,
    "夫妻": RelationKind.COUPLE,
    "恋人": RelationKind.COUPLE,
    "伴侣": RelationKind.COUPLE,
    # 势力族
    "同门关系": RelationKind.SAME_SECT,
    "同宗": RelationKind.SAME_SECT,
    "成员": RelationKind.MEMBER,
    "属下": RelationKind.SUBORDINATE,
    "下属": RelationKind.SUBORDINATE,
    "上司": RelationKind.SUBORDINATE,
    "上级": RelationKind.SUBORDINATE,
    "忠诚": RelationKind.LOYAL,
    "效忠关系": RelationKind.LOYAL,
    "叛逃": RelationKind.DEFECTED,
    "背叛": RelationKind.DEFECTED,
    "通缉犯": RelationKind.WANTED_BY,
    "盟友关系": RelationKind.ALLIANCE,
    "同盟": RelationKind.ALLIANCE,
    "从属": RelationKind.VASSAL,
    "世仇关系": RelationKind.FEUD,
    "恩怨": RelationKind.DEBT,
    "欠债": RelationKind.DEBT,
    "誓言": RelationKind.OATH,
}


def normalize_relation_kind(raw: str) -> RelationKind:
    """把任意写法归一为 `RelationKind`。

    **未知类型抛错而不是静默降级** —— 与 `normalize_entity_kind` 同一条理由：
    静默降级会让"关系单一"这个痛点**复发而不留痕**
    （旧项目正是降到"其他"后没人发现）。

    调用方（持久化层）应捕获此异常并**记录告警**，
    落到 `CUSTOM` 只作为"不中断整章"的容错，而不是当成正常路径。
    """
    if not raw:
        raise ValueError("关系类型为空")
    key = raw.strip()
    try:
        return RelationKind(key)
    except ValueError:
        pass
    if key in _RELATION_KIND_ALIASES:
        return _RELATION_KIND_ALIASES[key]
    raise ValueError(
        f"未登记的关系类型 {raw!r}。请加入 RelationKind 或 "
        f"_RELATION_KIND_ALIASES（禁止静默降级）"
    )


class RelationStatus(StrEnum):
    ACTIVE = "active"
    CHANGED = "changed"
    BROKEN = "broken"


class AffiliationLeaveReason(StrEnum):
    DEFECTED = "叛变"
    LEFT = "脱离"
    EXPELLED = "被逐"
    FACTION_FELL = "势力覆灭"


class BeatType(StrEnum):
    """章节目拍类型。

    ★ 关键新增：`HEAL` / `RECOVERY_PREP`。
    旧项目 `VALID_BEATS = {"normal","boss","climax","emotion","recall","turning"}`
    （`schemas.py:25`）**没有 heal** → **恢复不是合法节拍类型**，
    细纲规划者无法把"疗伤"排成一章的任务 → 这是伤势恢复率 0% 的直接原因之一。
    """

    NORMAL = "normal"
    OPENING = "opening"
    TURNING = "turning"
    CLIMAX = "climax"
    BOSS = "boss"
    EMOTION = "emotion"
    RECALL = "recall"
    SETTLE = "settle"
    #: ★ 恢复章 —— 伤势在本章痊愈/解除
    HEAL = "heal"
    #: ★ 恢复铺垫章 —— 寻医/求药/疗伤尝试（解决"恢复太突然"）
    RECOVERY_PREP = "recovery_prep"
    #: 换场/过渡章 —— 旧项目把换图一律判为缺陷，这里显式建模
    TRANSITION = "transition"


class UsageSource(StrEnum):
    """用量数据来源 —— 继承旧项目已验证的正确契约。

    旧项目 `observability.py:118`：`native` 只要 **≥3/5 字段**即可，
    导致 33 行 `native` 的 `cached_tokens` 为 NULL → 成本函数
    **既不收 hit 也不收 miss**（**漏报**而非多报）。
    本设计：缺失字段必须显式标 `UNAVAILABLE`，不得静默按 0 计。
    """

    #: 全部字段来自 provider 原生返回
    NATIVE = "native"
    #: 部分字段原生、部分缺失
    PARTIAL = "partial"
    #: 完全拿不到
    UNAVAILABLE = "unavailable"


class CallStatus(StrEnum):
    """单次 LLM 调用结果。

    旧项目实测 `ok 772 / blank 223 / failed 8` —— **空输出率 22.2%**，
    且 `entity_classify` **100% blank**（32/32）、`verifier` 62.3%。
    `blank` 浪费的 token 占全部 token 的 **14.36%**（2,602,167）。
    """

    OK = "ok"
    #: 有请求、有 token 消耗、**零有效产出**
    BLANK = "blank"
    FAILED = "failed"
    TIMEOUT = "timeout"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkUnitStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    #: 到达人工确认点，等待中（**不阻塞其他 worker**，决策 D-13）
    WAITING_CONFIRM = "waiting_confirm"
    DONE = "done"
    FAILED = "failed"
    #: 崩溃后标记，需重建
    ORPHANED = "orphaned"


class PipelineStage(StrEnum):
    """三级流水线的阶段（决策 D-13）。"""

    BIBLE = "bible"
    VOLUME_PLAN = "volume_plan"
    SKELETON = "skeleton"
    DETAIL = "detail"
    CHAPTER = "chapter"


class ConfirmPoint(StrEnum):
    """人工确认点（决策 D-14 + 阶段 2-3 的 learn 扩展）。

    ## story 侧（C-1~C-7，决策 D-14）

    强制（5 个，各全书 1 次，除卷末外）：
      C-1 圣经定稿 / C-2 骨架首批 / C-3 首章细纲 / C-4 全卷升级台阶 / C-5 黄金三章
    条件触发（2 个）：
      C-6 核心实体不可逆消失 / C-7 质量闸门连续失败

    ## ★ learn 侧（L-1~L-2，阶段 2-3，用户决策：**独立命名空间**）

        L-1 知识点确认       —— 联网研究产出的"要教什么"
        L-2 平替修辞确认     —— "怎么说成世界内的话"

    ### 为什么另开 L- 前缀而不是挤进 C-8/C-9

    两条理由：

    1. **顺序一致性**：L-1/L-2 发生在 C-1（圣经定稿）**之前** ——
       知识点与说法先定，圣经才知道该写什么世界。
       若叫 C-8/C-9，编号顺序（C-1 在 C-8 前）会与流程顺序相反，
       而编号顺序正是阅读代码时的第一直觉。
    2. **story / learn 低耦合**（用户明确要求）：两套确认点各成一体，
       story 链路永远不会看到 L-*，learn 链路也不会被 C-* 干扰。

    ### 为什么是两个而不是一个（用户决策：逐条确认）

    L-1 确认「教什么」、L-2 确认「怎么说」是两件事。
    混在一次确认里，用户改一个知识点会连带要重看整张映射表。
    实际实现上 L-2 是**逐条**确认（每条映射独立 accept/reject），
    见 `domain/learning.py` 的 `TermMapping.accepted`。
    """

    C1_BIBLE = "C1_bible"
    C2_SKELETON_FIRST_BATCH = "C2_skeleton_first_batch"
    C3_FIRST_DETAIL = "C3_first_detail"
    C4_VOLUME_LADDER = "C4_volume_ladder"
    C5_GOLDEN_THREE = "C5_golden_three"
    C6_IRREVERSIBLE_LOSS = "C6_irreversible_loss"
    C7_QUALITY_GATE = "C7_quality_gate"

    # ── learn 专属（阶段 2-3）──
    L1_KNOWLEDGE_POINTS = "L1_knowledge_points"
    L2_TERM_MAPPINGS = "L2_term_mappings"


#: 强制确认点（每次运行都会触发）
MANDATORY_CONFIRMS: frozenset[ConfirmPoint] = frozenset(
    {
        ConfirmPoint.C1_BIBLE,
        ConfirmPoint.C2_SKELETON_FIRST_BATCH,
        ConfirmPoint.C3_FIRST_DETAIL,
        ConfirmPoint.C4_VOLUME_LADDER,
        ConfirmPoint.C5_GOLDEN_THREE,
        # ★ learn 的两个是**强制**的：知识点与说法没定就不该开写
        #   （它们发生在 C-1 之前，见类 docstring 的顺序说明）
        ConfirmPoint.L1_KNOWLEDGE_POINTS,
        ConfirmPoint.L2_TERM_MAPPINGS,
    }
)

#: 条件触发确认点（仅在特定条件出现时）
CONDITIONAL_CONFIRMS: frozenset[ConfirmPoint] = frozenset(
    {ConfirmPoint.C6_IRREVERSIBLE_LOSS, ConfirmPoint.C7_QUALITY_GATE}
)

#: ★ learn 专属确认点 —— story 链路永远不会看到它们（两套各成一体）
LEARN_CONFIRMS: frozenset[ConfirmPoint] = frozenset(
    {ConfirmPoint.L1_KNOWLEDGE_POINTS, ConfirmPoint.L2_TERM_MAPPINGS}
)

#: ★ story 专属确认点（D-14 原有的 7 个）
STORY_CONFIRMS: frozenset[ConfirmPoint] = (
    MANDATORY_CONFIRMS | CONDITIONAL_CONFIRMS
) - LEARN_CONFIRMS

assert MANDATORY_CONFIRMS | CONDITIONAL_CONFIRMS == set(ConfirmPoint), (
    "每个确认点必须属于「强制」或「条件触发」之一 —— "
    "新增确认点时两处都要登记，否则它会永远不触发"
)
assert not (STORY_CONFIRMS & LEARN_CONFIRMS), (
    "story 与 learn 的确认点不得重叠（用户要求两区域低耦合）"
)
assert STORY_CONFIRMS | LEARN_CONFIRMS == set(ConfirmPoint)


class TeachingStatus(StrEnum):
    """知识点教学生命周期（三态）—— learn 阶段 2-4。

    ## 为什么必须有第三态（本项目此前缺的）

    旧项目 `KnowledgeLedger.status` 有 **`未讲授 / 已讲授 / 已复用`** 三态，
    而本项目阶段 1 的 `modes/protocols.py:TeachingRecord` 只有
    `point_id / taught_chapter / review_count / next_review_chapter` ——
    **缺"毕业"**。

    不补的后果：`next_review_chapter=None` 既表示"已毕业"也表示"尚未排期"，
    两者无法区分 → **毕业的知识点会被反复挑中复习**。

    ## 三态的转换（由 `domain/teaching.py:TeachingLedger` 驱动）

        UNTAUGHT --record_taught--> TAUGHT --advance×len(intervals)--> MASTERED

    其中 `advance` 只在**复习点真正出现在正文里**时调用
    （移植旧项目 `advance_reviews` 的 `used` 判定）。
    """

    UNTAUGHT = "未讲授"
    TAUGHT = "已讲授"
    #: 复习轮次走完全部间隔 —— **毕业**，不再被挑中
    MASTERED = "已复用"


class HealthAction(StrEnum):
    """伤势节拍动作（对策④）—— 恢复必须由**细纲场景**承载。"""

    #: 新发/恶化（伤害发生后）
    INFLICTED = "inflicted"
    SEEK_TREATMENT = "seek_treatment"
    ACQUIRE_MEDICINE = "acquire_medicine"
    CONDITION_WORSENS = "condition_worsens"
    TREATMENT_ATTEMPT = "treatment_attempt"
    RECOVERING = "recovering"
    HEALED = "healed"
    #: 明示永久化（不可恢复的伤需显式声明，而非无限"维持"）
    MADE_PERMANENT = "made_permanent"


class GateErrorCode(StrEnum):
    """结构闸门失败分类。

    ★ 必须分类的原因：旧项目实测**禁止全量重写结构 JSON**
      —— 大输出必被模型截断（book23/25 两次 ROLLED_BACK 的根因）。
      修复必须按 error_code 生成**增量补缺**的小 prompt。

    （`docs/AGENTS.md`：补丁按 `arcs`/`beats`/`capacity`/`milestones`/
      `factions`/`characters`/`volumes` 分类，各自小 prompt。）
    """

    VOLUME_COUNT = "volumes"
    CHAPTER_DISTRIBUTION = "chapter_distribution"
    NEW_LOCATIONS = "new_locations"
    NEW_FACTIONS = "new_factions"
    ANTAGONIST_VARIETY = "antagonist"
    ESCALATION_QUALITY = "escalation"
    MILESTONE_DENSITY = "milestones"
    LOCATION_REUSE = "location_reuse"
    OLD_MAP_CLOSURE = "old_map_closure"


class LocationLevel(StrEnum):
    """地点层级（解决旧项目的字符串碎片化）。

    旧项目书 23 的地点被拆成 **40+ 个字符串变体**
    （`长明楼三层值班室` / `长明楼三楼档案科资料室` / `长明楼三层走廊` /
      `长明楼三层值班室门外`），且 **77 次 location 为空**。
    """

    WORLD = "world"
    COUNTRY = "country"
    REGION = "region"
    CITY = "city"
    BUILDING = "building"
    FLOOR = "floor"
    ROOM = "room"


class EndingPattern(StrEnum):
    """章末类型（打分卡用）。

    旧项目实测书 23 的分布：`mid_action 23 / other 21 / omen 2 / dialogue 1 /
    summary 0 / time_skip 0` —— 注意 `summary` 与 `time_skip` 为 0，
    说明旧项目「不要总结式结尾」的规则**确实生效了**
    （所以 `02-根因与架构对策` 的「不要做」清单里有
      「不要把总结式结尾当硬伤」）。
    """

    MID_ACTION = "mid_action"
    DIALOGUE = "dialogue"
    OMEN = "omen"
    SUMMARY = "summary"
    TIME_SKIP = "time_skip"
    OTHER = "other"


class TriggerReason(StrEnum):
    """打分卡触发 LLM 审校/重写的理由（决策 D-10）。

    分三类：
      · 无条件触发（黄金三章 / 卷末 / 高潮）—— D-14 的质量门禁
      · hard 层阈值触发 —— 明确缺陷，少而准
      · 总分触发（`LOW_SCORE`）—— target 层水位未达
    """

    # ── 无条件触发 ──
    GOLDEN_THREE = "golden_three"          # 黄金三章（D-14 C-5）
    VOLUME_END = "volume_end"              # 卷末章
    CLIMAX_BOSS = "climax_boss"            # 高潮/Boss 章

    # ── hard 层阈值触发 ──
    CROSS_OVERLAP = "cross_overlap"        # 与近 N 章重复度过高
    INTRA_REDUNDANCY = "intra_redundancy"  # 章内冗余过高
    DIALOGUE_LOW = "dialogue_low"          # 对话占比过低
    CLICHE_HEAVY = "cliche_heavy"          # 套话密集
    SIMILE_HEAVY = "simile_heavy"          # 比喻过量
    EMOTION_NAMING = "emotion_naming"      # 情绪直陈过多
    SENTENCE_UNIFORM = "sentence_uniform"  # 句长过于均匀
    OUTLINE_DEVIATION = "outline_deviation"  # 细纲未落实
    HOOK_MISSED = "hook_missed"            # 章末未落在钩子
    STATE_CONFLICT = "state_conflict"      # 状态矛盾
    UNSUPPORTED_ENTITY = "unsupported_entity"  # 无依据新名词
    ENDING_OMEN = "ending_omen"            # 假钩子（贴片式无主暗示）
    AI_SCENE_ENDING = "ai_scene_ending"    # AI 画面收尾（微笑/望远方，只看末 60 字）
    BRIDGE_BREAK = "bridge_break"          # 章首时间硬切
    TOO_SHORT = "too_short"                # 字数不足

    # ── 总分触发 ──
    LOW_SCORE = "low_score"

    # ── ★ learn（知识小说）专属触发（2026-09-24，阶段 2-5）──
    #:
    #: ★ 为什么必须新增而不是复用 story 的项：复用会让**触发名说谎** ——
    #: 例如把"平替修辞命中率过低"记成 `CLICHE_HEAVY`（套话密集），
    #: 报告里看到的理由与实际原因不符。本项目明令禁止这类误导
    #: （见 `_summarize_validation_error` 的同类教训：
    #: 「报表丢掉了结构化的 code，改用措辞猜」）。
    #:
    #: ★ 也**不**使用 `DIALOGUE_LOW` / `GOLDEN_THREE` / `ENDING_OMEN`：
    #:   · `DIALOGUE_LOW` —— 教学章对话少是**正确**写法（实测误杀源）
    #:   · `GOLDEN_THREE` —— 网文留存策略，教学章无此概念
    #:   · `ENDING_OMEN` —— 教学章的总结收尾是**必需**，不是假钩子
    BARE_TERM = "bare_term"                  # 正文直出原术语（出戏）
    SUBSTITUTION_LOW = "substitution_low"    # 平替修辞命中率过低
    LOW_DENSITY = "low_density"              # 知识密度过低（灌水）
    EXPLANATION_SHALLOW = "explanation_shallow"  # 只提名词不讲透
    REVIEW_MISSED = "review_missed"          # 到期复习点未被讲到


__all__ = [
    "BookMode",
    "BookStatus",
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "EntityKind",
    "normalize_entity_kind",
    "LifecycleState",
    "PERMANENT_LOSS_STATES",
    "RECOVERABLE_STATES",
    "InjuryKind",
    "InjurySeverity",
    "InjuryStatus",
    "RECOVERY_STATUSES",
    "RelationKind",
    "RelationSentiment",
    "RelationStatus",
    "AffiliationLeaveReason",
    "BeatType",
    "UsageSource",
    "CallStatus",
    "RunStatus",
    "WorkUnitStatus",
    "PipelineStage",
    "ConfirmPoint",
    "MANDATORY_CONFIRMS",
    "CONDITIONAL_CONFIRMS",
    "LEARN_CONFIRMS",
    "STORY_CONFIRMS",
    "TeachingStatus",
    "HealthAction",
    "GateErrorCode",
    "LocationLevel",
    "EndingPattern",
    "TriggerReason",
]
