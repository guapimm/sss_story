"""SQLAlchemy 数据模型 —— 对照 doc/04-数据模型规格.md。

## 与旧项目的关键差异（每条都对应一次实测事故）

1. **强外键**：旧项目 27 张表 **0 个 FK**，删书靠人工清单 `BOOK_SCOPED_MODEL_NAMES`，
   注释自承**历史上漏过 6 张表**。本模块所有从属表都有 `ON DELETE CASCADE` FK。
2. **章号单一权威**：只有 `global_no`（全书递增 int）。旧项目 `vol*10000+idx` 与卷内号
   双表示，导致 `since_ch=10012-1=10011` → 每个伤每章都挂"必须推进" → Editor 每章低分。
3. **实体一等化**：一切专名有 `entities` 行与 ID。旧项目势力实体 **0 个**（因为
   `entities.py` 的 `ENTITY_TYPES` 不含「势力」→ LLM 输出被静默降级为「其他」）。
4. **四分离**：关系 / 归属 / 状态 / 事件是四张表。旧项目一张 `relationships` 混装，
   实测 **86.7% 是非人际关系**（「查看」「拾取物品」「值班」）。
5. **无死表**：旧项目 Phase 2 六张表生产 **0 行**（~1150 行代码 + 91 列负债）。
   本模块只建一期真正要写入的表。

## 时间字段

统一 `String` 存 **UTC ISO8601**，不用 `DateTime` —— 因 LangGraph `#8826`
会**静默丢时区**（doc/06 R-03c）。用 `utcnow_iso()` 生成。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from storyforge.domain.enums import (
    AffiliationLeaveReason,
    BeatType,
    BookMode,
    BookStatus,
    EntityKind,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
    LifecycleState,
    RelationKind,
    RelationSentiment,
    RelationStatus,
    RunStatus,
    UsageSource,
)


def utcnow_iso() -> str:
    """统一的 UTC ISO8601 时间戳字符串（不用 datetime 过 checkpoint）。"""
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# 书籍 / 卷 / 章节
# --------------------------------------------------------------------------- #


class Book(Base):
    """书籍主表。

    旧项目对照：`phase` 是无约束 TEXT 列、**43 处散落赋值、15 个状态**，
    且实测 `architecting` 不在 `ACTIVE_PHASES` → **重启后书永久卡死**。
    这里 `status` 是枚举 + 单一转换函数（见 domain/state_machine.py）。
    """

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default=BookMode.STORY.value)
    genre: Mapped[str] = mapped_column(String(40), nullable=False)
    scale: Mapped[str] = mapped_column(String(16), nullable=False)

    target_chapters: Mapped[int] = mapped_column(Integer, nullable=False)
    total_volumes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default=BookStatus.CREATED.value)
    current_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 已完成到第几章（全书递增号）；0 表示尚未开始
    current_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 大产物引用：正文/圣经存在文件里，DB 只存 key + hash
    # 旧项目 `books.bible` 列在重开生成时被清空，且新书只写 bible_path
    # → 每章 L0 圣经**静默为空**、世界观约束失效。故此处只保留 ref。
    bible_ref: Mapped[str | None] = mapped_column(String(200))
    bible_hash: Mapped[str | None] = mapped_column(String(64))

    #: 本作品使用的配置快照（JSON），用于复现
    config_snapshot: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)

    volumes: Mapped[list[Volume]] = relationship(
        back_populates="book", cascade="all, delete-orphan", order_by="Volume.volume_no"
    )


class Volume(Base):
    """卷 —— **升级台阶**是必填字段（对策② / 决策 D-07）。

    旧项目把卷信息塞在 `books.outline` 的 JSON 列里，导致后续卷
    `blueprint="missing"` + `main_line=""`，**400 章全挤第 1 卷**，
    而第 1 卷的 vol_goal 是「在一栋楼里读透三十七条」。

    这里把升级台阶做成 NOT NULL 列，模型无法通过"省略"来逃避扩张设计。
    """

    __tablename__ = "volumes"
    __table_args__ = (
        UniqueConstraint("book_id", "volume_no", name="uq_volume_book_no"),
        Index("ix_volumes_book", "book_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── 升级台阶（必填，structure_gate 校验其"实质"而非仅非空）──
    #: JSON list[str]：本卷新开地图（≥2 且不与既往重复）
    new_locations: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON list[str]：本卷新登场势力（≥2）
    new_factions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: 本卷主要对手（必须与既往卷不同）
    antagonist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    power_ceiling: Mapped[str] = mapped_column(Text, nullable=False, default="")
    core_conflict: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 相对上卷「升级在哪」—— 必须具体，禁止模板句
    escalation_from_prev: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 收尾 ──
    old_map_closure: Mapped[str] = mapped_column(Text, nullable=False, default="")
    volume_end_hook: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 章号区间（★ 冻结：定稿后不再变，保证已写章节显示标签稳定）──
    chapter_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_end: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 台阶验收（卷末自动校验；异常时触发人工确认 C-7）
    audit_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    audit_json: Mapped[str | None] = mapped_column(Text)

    book: Mapped[Book] = relationship(back_populates="volumes")


class Chapter(Base):
    """章节元数据 + 写作计划引用。

    ★ `global_no` 是**唯一**章号权威（决策 D-15）。
    卷内序号 = `global_no - volume.chapter_start + 1`，**派生不存储**。
    """

    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "global_no", name="uq_chapter_book_no"),
        Index("ix_chapters_book_status", "book_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    volume_id: Mapped[int | None] = mapped_column(
        ForeignKey("volumes.id", ondelete="SET NULL")
    )
    #: ★ 全书单调递增章号，所有比较/排序/去重只用它
    global_no: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    beat_type: Mapped[str] = mapped_column(String(24), nullable=False, default=BeatType.NORMAL.value)
    #: 是否换场章（供 lint/审校区分对待 —— 旧项目把"换图"一律判为缺陷）
    is_transition: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 细纲引用（大 JSON 存文件）
    plan_ref: Mapped[str | None] = mapped_column(String(200))
    # 正文引用（正文存文件，DB 存 ref + hash）
    # 旧项目 `content_path` **80% 为空**且 3 种格式混用，`read_chapter` 读不到时
    # **静默返回 ""** → 正文丢失不报错。这里 content_hash 可用于校验。
    content_ref: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    content_len: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: 打分卡总分（零 LLM 确定性）
    score: Mapped[float | None] = mapped_column(Float)
    scorecard_json: Mapped[str | None] = mapped_column(Text)
    review_json: Mapped[str | None] = mapped_column(Text)
    rewrite_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")

    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)


# --------------------------------------------------------------------------- #
# 实体（一等公民）
# --------------------------------------------------------------------------- #


class Entity(Base):
    """一等实体 —— 人 / 势力 / 地点 / 物品 / 功法 / 规则 / 术语。

    ## 为什么必须有这张表

    旧项目**没有实体表**：势力只以字符串出现在关系两端，
    实测 `relationships` 966 行中 `type_b='势力'` 仅 **7 行**、`'组织'` 14 行，**势力实体 0 个**。

    失效链（三套类型词汇表冲突）：
        `entities.py:17` 有「组织」无「势力」
        `models.py:267`   有「势力」
        `graph.py:36`     两者都无
        → LLM 吐 `type="势力"` → 判非法 → 降级「其他」→ 图谱再降一次

    本设计用**单一 EntityKind 枚举**消除该问题（`ORGANIZATION` 与 `FACTION` 合并）。

    ## 生命周期

    旧项目用**名字前缀**表达状态（`[毁·可修]青锋剑`），需要专门的 `_strip_life_prefix`，
    且剥离**顺序敏感**（`memory.py:668-669` 实证：`+[失·不可寻]家庭合影` 若先剥前缀
    会残留 `[失·不可寻]` 逃过归一化 → 误杀）。这里改为**独立枚举列**，无字符串解析。
    补充：旧项目 `[耗·一次性]` **既不在永久态也不在可恢复态**（`memory.py:216-220`），
    完全失管；本设计的 `CONSUMED` 是明确枚举值。
    """

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("book_id", "kind", "canonical_name", name="uq_entity_name"),
        Index("ix_entities_book_kind", "book_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: JSON list[str] 别名（含旧称、尊称、代号）
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    first_seen_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen_chapter: Mapped[int | None] = mapped_column(Integer)

    lifecycle: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LifecycleState.ACTIVE.value
    )
    #: JSON dict —— kind 专属属性，由 Pydantic 校验后写入
    attributes: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    #: 是否当前有效（死亡/覆灭后置 False，但**保留行**以便回忆/遗物引用）
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)


class Location(Base):
    """地点层级 —— 解决旧项目的字符串碎片化。

    旧项目书 23 的地点被拆成 **40+ 个字符串变体**
    （`长明楼三层值班室` / `长明楼三楼档案科资料室` / `长明楼三层走廊` / `长明楼三层值班室门外`），
    且 **77 次 location 为空**。

    层级化后可直接算「本卷新增几个 Location」（扩张闸门指标）
    与 `location_reuse_ratio`（防困在一处）。
    """

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("book_id", "entity_id", name="uq_location_entity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    #: world / country / region / city / building / floor / room
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="room")
    geography_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: 当前控制势力（可变 —— 随剧情易主）
    controlling_faction_id: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    is_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------- #
# 关系 / 归属 / 状态（四分离）
# --------------------------------------------------------------------------- #


class EntityRelation(Base):
    """**社会关系**（有情感/立场/权力语义，会演化）—— 不是动作流水账。

    旧项目一张 `relationships` 表混装三种语义，实测：
      · 人物-人物仅 **98/739 = 13.3%**
      · `relation_type` 是**自由文本**，254 条人物-人物边用了 **191 种**标签，
        自带 9 词受控词表只覆盖 **31/254 = 12.2%**
        → 系统无法聚合/比较/要求"关系多样性"（`敌对/对手/仇视/对立` 无法归一）

    本设计：`relation_kind` 是**枚举**；新增张力维度（intensity / sentiment /
    明暗分离 / 非对称）解决痛点 4「人物关系单一」。
    """

    __tablename__ = "entity_relations"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "from_entity_id", "to_entity_id", "relation_kind",
            name="uq_relation_edge",
        ),
        Index("ix_relations_from", "book_id", "from_entity_id"),
        Index("ix_relations_to", "book_id", "to_entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    from_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    to_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    relation_kind: Mapped[str] = mapped_column(String(24), nullable=False)

    # ── 张力维度（旧项目完全没有）──
    intensity: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    sentiment: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RelationSentiment.NEUTRAL.value
    )
    #: 世人所知的关系表现（旧项目 visibility=明）
    surface_facing: Mapped[str | None] = mapped_column(Text)
    #: 真实立场（旧项目 visibility=暗）—— 两者不同即有暗线张力
    true_facing: Mapped[str | None] = mapped_column(Text)
    #: 非对称关系：A→B 与 B→A 是两条独立记录
    is_mutual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── 演化 ──
    since_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RelationStatus.ACTIVE.value
    )
    changed_by_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL")
    )
    updated_chapter: Mapped[int] = mapped_column(Integer, nullable=False)


class EntityAffiliation(Base):
    """**归属**（成员关系，带时间区间）—— 不是可累积的边。

    旧项目归属走关系表，且更新用「五元组匹配」**把 `type_b` 当匹配键**
    （`memory.py:2763-2779`）→ LLM 本次写「组织」下次写「势力」即匹配失败
    → 旧边**不被解除**、新边**继续累积**。实测 `顾长生→执法殿`
    同时存在「归属」与「逃避/即将对峙」两条并存。

    本设计：归属有 `joined_chapter` / `left_chapter` 时间区间，
    天然表达"何时加入、何时离开"，不需要解除旧边。
    """

    __tablename__ = "entity_affiliations"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "entity_id", "faction_id", "joined_chapter", name="uq_affiliation"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    faction_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str | None] = mapped_column(String(100))
    joined_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    #: NULL = 仍在
    left_chapter: Mapped[int | None] = mapped_column(Integer)
    leave_reason: Mapped[str | None] = mapped_column(String(32))


class EntityState(Base):
    """**实体状态**（位置/境界/目标/情绪/装备/性格…）—— 字段级，非大 JSON。

    旧项目是 `character_states.state` 一个 JSON 大对象（77 行、21 个字段混装）。
    字段级后：可查询、可比较、可做状态连续性校验。
    `tier` 继承旧项目已验证的字段分级（immutable 严禁改动 / event 需事件支撑 /
    mono_list 只增不减 / inventory / realm / scalar）。
    """

    __tablename__ = "entity_states"
    __table_args__ = (
        UniqueConstraint("book_id", "entity_id", "field", name="uq_entity_state_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    #: JSON 值
    value: Mapped[str] = mapped_column(Text, nullable=False, default="null")
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="scalar")
    updated_chapter: Mapped[int] = mapped_column(Integer, nullable=False)


class Injury(Base):
    """**伤势** —— 独立表 + 恢复计划（对策④，痛点 1）。

    ## 旧项目的实测状态（恢复率 0%）

    · 伤势**不是表**，是 `CharacterState.state` JSON 里的 4 个键
    · 36 条损伤中 `status` **从未出现「痊愈」「解除」** → 恢复率实测 **0%**
    · `architect.py`（细纲规划）中 `伤势|损伤` **0 命中** → 细纲层根本不规划伤
    · `VALID_BEATS` 无 `heal`/`recovery` → 恢复不是合法节拍

    ## 根因（能力矩阵）

        | 角色          | 能看到损伤 | 能规划治疗 | 能落实治疗 |
        | architect     | ❌         | ❌        | —         |
        | writer        | ✅         | ❌ 受细纲约束 | ⚠️ 只能照细纲写 |
        | post_processor| ✅         | ❌        | ❌        |
        | editor        | ✅         | ❌        | ⚠️ 只能打回重写 |

        **没有任何一个 agent 同时具备「看到损伤」和「规划本章节拍」两个能力。**

    ## 本设计的三条硬约束

    1. `expected_heal_chapter` 由 `HealthScheduler` **规划**出来（不是每章 prompt 催促）
    2. `heal_scene_planned=True` 才允许注入"需推进"提示（**消除旧项目的死锁**）
    3. 报「痊愈」必须有结构化 `health_beats` 支撑（不再靠自由文本关键词匹配 ——
       旧项目的守卫要求"角色名 + 治疗语义词同时出现在同一次 LLM 输出的 events 里"，
       而 prompt 只让写 2~5 条关键事件 → **过渡章必然自锁**）
    """

    __tablename__ = "injuries"
    __table_args__ = (Index("ix_injuries_entity_active", "book_id", "entity_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)

    #: ★ 单一章号
    since_chapter: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── 恢复计划（旧项目完全没有）──
    expected_heal_chapter: Mapped[int | None] = mapped_column(Integer)
    heal_scene_planned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    heal_condition: Mapped[str | None] = mapped_column(Text)

    is_permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_chapter: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #


class Event(Base):
    """**事件**（动作流水账的正确归宿）。

    旧项目把「陈砚查看录音笔」「郑维值班」这类**动作**写进了关系表
    → 关系表 86.7% 被非人际关系占用。动作属于事件，不属于关系。
    """

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_chapter", "book_id", "global_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    #: ★ 单一章号
    global_no: Mapped[int] = mapped_column(Integer, nullable=False)
    story_time: Mapped[str | None] = mapped_column(String(100))
    #: 地点实体引用（**不是字符串** —— 旧项目地点是 40+ 个碎片字符串）
    location_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    consequence: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)


class EventParticipant(Base):
    """事件参与者 —— 关联表 + FK，替代旧项目的 `participants` JSON 字符串数组。

    旧项目实测：书 23 第 1 章所有事件的 `participants` 为 `[[]]`（**空数组**），
    尽管描述里明确写了"郑维向陈砚交班"。关联表 + FK 可强制完整性。
    """

    __tablename__ = "event_participants"
    __table_args__ = (Index("ix_event_participants_entity", "entity_id"),)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    #: 主体 / 客体 / 目击者
    role: Mapped[str | None] = mapped_column(String(24))


class Foreshadowing(Base):
    """伏笔 + **回收排程**。

    旧项目实测长篇回收率 **6%~23%**（书 8 仅 6%）。根因之一是卷末
    **无条件** `_force_recover_fs`（`pipeline.py:5994 → 5456`）把贯穿/长期伏笔
    **并入末章核销**，且 ultra 要求 0 未回收 → **第 2 卷末就清空，后续无长线可收**。

    本设计：`is_long_term` 标记贯穿型伏笔，**禁止卷末强制核销**。
    """

    __tablename__ = "foreshadowings"
    __table_args__ = (
        UniqueConstraint("book_id", "fs_key", name="uq_fs_key"),
        Index("ix_fs_unresolved", "book_id", "resolved_chapter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    fs_key: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="short")
    weight: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")

    #: ★ 单一章号
    plant_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 计划回收章（旧项目只有 resolve_ch，且卷末被无条件清空）
    planned_resolve_chapter: Mapped[int | None] = mapped_column(Integer)
    resolved_chapter: Mapped[int | None] = mapped_column(Integer)
    #: ★ 贯穿型：禁止卷末强制核销
    is_long_term: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# --------------------------------------------------------------------------- #
# 运行 / 工作单元 / 观测
# --------------------------------------------------------------------------- #


class Run(Base):
    """一次书籍生成运行（协调器的顶层记录）。"""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=RunStatus.RUNNING.value)
    started_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    finished_at: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)


class WorkUnit(Base):
    """一个**工作单元** = 一次 LangGraph 图调用（决策 D-13）。

    协调器用它做队列、闸门与崩溃恢复。每单元对应一个 `thread_id`，
    因此恢复是**单元级**的 —— 只重跑未完成的那一批，不重跑整个阶段。

    `idempotency_key` 解决 LangGraph `#7417`：>180s 的节点会被**静默重放**
    （53 条评论仍 open），章节生成极易超阈值 → 必须有幂等键复用已有结果。
    """

    __tablename__ = "work_units"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_work_unit_idem"),
        Index("ix_work_units_stage_status", "book_id", "stage", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    #: bible / volume_plan / skeleton / detail / chapter
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    #: 图名（与 graph/graphs/ 对应）
    graph_name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 批次序号（骨架/细纲用）或章号（正文用）
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    volume_no: Mapped[int | None] = mapped_column(Integer)

    #: ★ 幂等键：(book_id, stage, volume, seq, attempt)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    #: ★ LangGraph thread_id —— 每单元独立 checkpoint 命名空间
    thread_id: Mapped[str] = mapped_column(String(120), nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    #: 人工确认点（C-1..C-7）；非空表示正在等待人工
    pending_confirm: Mapped[str | None] = mapped_column(String(16))
    result_ref: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)


class LLMCall(Base):
    """LLM 调用观测（对策⑦）—— 旧项目三张表合并为一张。

    旧项目 `generation_attempts` + `generation_spans` + `metrics` **字段重叠、
    同一次调用记两遍**；4 张遥测表占全库 **54%** 且**无 TTL**。

    ## 四个旧项目完全缺失的字段（必须采集）

    · `finish_reason` —— 旧项目 **100% 未采集** → 无法区分"模型主动停"与
      "被 max_tokens 截断"，**这是诊断 22.2% 空输出的前提**
    · `context_breakdown` —— 逐来源拆分输入 token（旧项目只有字符数且覆盖率 **0.76%**）
    · `prompt_prefix_hash` —— 分析缓存命中/未命中的依据
    · `node_name` —— 旧项目 `GenerationAttempt` **无 chapter 列**，
      导致**只有 3.2% 的 trace 能按章算钱**

    ## 语义陷阱（旧项目实测的 cost 计算错误）

    `reasoning_tokens ⊆ completion_tokens`（896/897 行验证 `prompt+completion == total`）。
    旧项目 `trace_service.py:117-120` 算 `completion + reasoning`
    → **多收 77% 的输出费**。本系统禁止相加。
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_run", "run_id"),
        Index("ix_llm_calls_book_chapter", "book_id", "global_no"),
        Index("ix_llm_calls_node", "node_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    #: ★ 单一章号（旧项目缺此列 → 无法按章归因成本）
    global_no: Mapped[int | None] = mapped_column(Integer)

    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)

    # ── 用量（NULL 表示缺失，**不冒充 0**）──
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    #: ★ 语义：⊆ completion_tokens，**不得相加**
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UsageSource.UNAVAILABLE.value
    )
    usage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── ★ 旧项目缺失的诊断字段 ──
    finish_reason: Mapped[str | None] = mapped_column(String(40))
    #: JSON {bible: N, outline: N, ...}，单位 **token**（旧项目是字符且覆盖 0.76%）
    context_breakdown: Mapped[str | None] = mapped_column(Text)
    prompt_prefix_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_total_chars: Mapped[int | None] = mapped_column(Integer)

    elapsed_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_reason: Mapped[str | None] = mapped_column(String(200))
    error_type: Mapped[str | None] = mapped_column(String(120))

    started_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    finished_at: Mapped[str | None] = mapped_column(String(40))


__all__ = [
    "Base",
    "utcnow_iso",
    "Book",
    "Volume",
    "Chapter",
    "Entity",
    "Location",
    "EntityRelation",
    "EntityAffiliation",
    "EntityState",
    "Injury",
    "Event",
    "EventParticipant",
    "Foreshadowing",
    "Run",
    "WorkUnit",
    "LLMCall",
]
