"""仓储层 —— **业务代码访问数据的唯一入口**（决策：L3 不得直接写 SQL）。

## 为什么必须有这一层

旧项目**没有 Repository / DAO 层**：实测 **145 处裸 `db_session()`** 直连，
散落在 `main.py`(55)、`pipeline.py`(28) 等 20+ 模块；
`models.py:664` 就是全部"访问层"，且只返回裸 `Session`。

后果是口径无法收敛 —— 典型实例：
`since_ch` 在写入侧存**卷内号**、消费侧按**全书号**比对
→ `elapsed = 10012 - 1 = 10011` → 阈值仅 1~3 → **每章恒真**。

本层把 row ↔ domain 转换**集中**，口径只有一处（`doc/04-数据模型规格.md:428-431`）。

## 分层约束（契约测试强制）

    L0 storage（SQLAlchemy models）
    L1 infra（**本层**：仓储）
    L2 domain / scorecard  ← 不得 import infra.llm 或 graph
    L3 graph
    L4 orchestrator
    L5 api / cli

`domain/` **不得** import 本层 —— 领域对象零框架零 SQL。
本层负责把 row 转成领域 dataclass。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from storyforge.infra.storage.db import Database

T = TypeVar("T")


class Repository(Generic[T]):
    """仓储基类 —— 持有一个 `Database`，提供会话与通用查询。

    子类只需声明 `model`（SQLAlchemy 类）与实现 `to_domain` / `from_domain`。

    ⚠️ 本基类**不**暴露裸 `Session` 给业务代码 ——
    那是旧项目 145 处直连的入口。子类用 `self.session()` 上下文管理器。
    """

    #: SQLAlchemy 模型类（子类必须覆盖）
    model: type[Any]

    def __init__(self, db: Database) -> None:
        self.db = db

    def session(self):
        """事务性会话（正常提交、异常回滚）。"""
        return self.db.session()

    # -- 通用查询 -----------------------------------------------------------

    def get(self, row_id: int) -> T | None:
        """按主键取一个，转成领域对象。"""
        with self.session() as sess:
            row = sess.get(self.model, row_id)
            return None if row is None else self.to_domain(row)

    def list_all(self) -> list[T]:
        with self.session() as sess:
            rows = sess.execute(select(self.model)).scalars().all()
            return [self.to_domain(r) for r in rows]

    def count(self) -> int:
        with self.session() as sess:
            return len(sess.execute(select(self.model)).scalars().all())

    # -- 转换（子类实现）----------------------------------------------------

    def to_domain(self, row: Any) -> T:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 to_domain")
