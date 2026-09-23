"""仓储层 —— 业务代码访问数据的**唯一入口**。

## 分层（契约测试强制）

    L0 storage（SQLAlchemy models）
    L1 infra（**本层**）
    L2 domain / scorecard   ← 不得 import infra.llm 或 graph
    L3 graph                ← 不得直接写 SQL（须经本层）
    L4 orchestrator
    L5 api / cli

`domain/` **不得** import 本层 —— 领域对象零框架零 SQL。

## 为什么存在

旧项目**没有 Repository / DAO 层**：**145 处裸 `db_session()`** 直连，
散落在 `main.py`(55) / `pipeline.py`(28) 等 20+ 模块。
口径无法收敛的典型后果：`since_ch` 写入侧存卷内号、消费侧按全书号比对
→ 阈值判定**每章恒真** → Editor 每章判「必推进的损伤未推进」。

## 一期范围（阶段 0）

只建**伤势接线所需的最小集合**（用户决策）：

    BookRepository    → `books`（FK 根；此前是死表，全仓无写入路径）
    EntityRepository  → `entities`（伤势 FK 到它，必须先有实体）
    InjuryRepository  → `injuries`（**痛点 1 的落地环**）

其余表（relations / chapters / volumes…）按需再补，不预先铺开。

## ★ 阶段 2 补充：`RelationRepository`

`relations` 是**痛点 2/4（实体·势力追踪混乱 / 人物关系单一）的落地环**。
在此之前 `entity_relations` 表**零写入路径** —— 抽取节点每章都抽关系，
但结果只留在内存/产物文件里（`chapter_nodes.py:383`），
**从未落库**。本仓储接通它。
"""

from storyforge.infra.storage.repositories.base import Repository
from storyforge.infra.storage.repositories.books import BookRecord, BookRepository
from storyforge.infra.storage.repositories.entities import (
    EntityRecord,
    EntityRepository,
)
from storyforge.infra.storage.repositories.injuries import InjuryRepository
from storyforge.infra.storage.repositories.relations import RelationRepository

__all__ = [
    "Repository",
    "BookRecord",
    "BookRepository",
    "EntityRecord",
    "EntityRepository",
    "InjuryRepository",
    "RelationRepository",
]
