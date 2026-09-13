"""数据库引擎与会话 —— 强制开启外键。

## 为什么这个模块必须存在

旧项目有两层外键问题：
  1. **0 个 FOREIGN KEY**（27 张表一个都没有）→ 删书靠人工清单
     `BOOK_SCOPED_MODEL_NAMES` 维护，注释自承**历史上漏过 6 张表**、
     生产已产生 trace 孤儿行。
  2. 即使建了 FK，**SQLite 默认不强制外键** —— 必须每个连接显式
     `PRAGMA foreign_keys=ON`。

本模块用 SQLAlchemy 的 `connect` 事件在每个连接上开启它，
并有测试断言 `PRAGMA foreign_keys` 返回 1（见 tests/unit/test_storage.py）。

## WAL 模式

三级流水线有多个 worker（骨架/细纲/正文）并发读写。
WAL 允许"多读一写"并发；配合「阶段内串行」的约束
（每阶段 worker 数 = 1，且不同阶段写不同表），无跨 worker 写冲突。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from storyforge.config import Settings, get_settings
from storyforge.infra.storage.models import Base


def _enable_sqlite_pragmas(dbapi_conn, _record) -> None:
    """每个 SQLite 连接都必须显式开启外键 —— 这是默认关闭的。

    同时开启 WAL（多 worker 并发读）与合理的同步级别。
    """
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
    finally:
        cursor.close()


def create_db_engine(db_path: Path | str, *, echo: bool = False) -> Engine:
    """创建引擎并挂上 PRAGMA 钩子。"""
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, echo=echo, future=True)
    event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


class Database:
    """数据库门面：引擎 + 会话工厂 + 建表。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    # -- 构造 --------------------------------------------------------------

    @classmethod
    def open(cls, db_path: Path | str, *, echo: bool = False) -> Database:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(create_db_engine(path, echo=echo))

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Database:
        s = settings or get_settings()
        s.ensure_dirs()
        return cls.open(s.db_path)

    # -- schema ------------------------------------------------------------

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    # -- 会话 --------------------------------------------------------------

    @contextmanager
    def session(self) -> Iterator[Session]:
        """事务性会话：正常退出提交，异常回滚。

        注意：**不要**在业务代码里到处 `db_session()` 直连 ——
        旧项目有 **145 处**裸 `db_session()` 散落（main.py 55 / pipeline.py 28），
        且 `models.py:664` 就是全部访问层（返回裸 Session，**零 DAO**）。
        新设计：业务代码经 `repositories/` 访问数据。
        """
        sess = self._session_factory()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # -- 诊断 --------------------------------------------------------------

    def foreign_keys_enabled(self) -> bool:
        with self.engine.connect() as conn:
            return bool(conn.execute(text("PRAGMA foreign_keys")).scalar())

    def journal_mode(self) -> str:
        with self.engine.connect() as conn:
            return str(conn.execute(text("PRAGMA journal_mode")).scalar())

    def check_integrity(self) -> str:
        with self.engine.connect() as conn:
            return str(conn.execute(text("PRAGMA integrity_check")).scalar())

    def list_tables(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ).fetchall()
        return [r[0] for r in rows]

    def foreign_key_targets(self, table: str) -> list[tuple[str, str]]:
        """返回 (引用列, 目标表) 列表 —— 供契约测试断言 FK 存在。"""
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA foreign_key_list({table})")).fetchall()
        # PRAGMA 列序: id, seq, table, from, to, on_update, on_delete, match
        return [(r[3], r[2]) for r in rows]


__all__ = ["create_db_engine", "Database"]
