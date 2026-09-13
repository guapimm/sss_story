"""产物存储 —— 大产物**不入图 state、不入 DB 大列**（决策 D-11）。

## 为什么必须外置

1. **LangGraph checkpoint 膨胀**：官方承认 200 轮 **5.3 GB**；
   社区实测 32 线程 **1.24 GB**。
2. **旧项目的同构问题**：把 400 章 `outline` 塞进 `books.outline` 单列。

## 旧项目的三个存储缺陷（本模块的对策）

| 缺陷 | 实测 | 对策 |
|---|---|---|
| `content_path` **80% 为空**（1,147/1,441 章） | 且路径有 **3 种格式**混用（`story/books/`、`learn/books/`、`books/`，**同一本书内也混用**） | 路径**由代码统一生成**（单一函数），不允许手工拼接 |
| `read_chapter` 读不到时**静默返回 `""`** | `docstore.py:212` → **正文丢失不报错** | **读取失败必须抛错** |
| `rag.json` 整份重写（book 8 达 **27.75 MB**） | 286 章约 **2.9 GB** 磁盘写 | 索引**增量更新**；本模块提供 append 语义 |

## 路径约定

    data/artifacts/books/{book_id}/
      bible.md                        世界观圣经
      series_plan.json                全卷升级台阶
      skeleton/v{vol:03d}.json        卷骨架
      plans/{global_no:05d}.json      章细纲
      prose/{global_no:05d}.txt       正文
      reviews/{global_no:05d}.json    审校结果
      extractions/{global_no:05d}.json 抽取结果

**统一由 `ArtifactStore` 生成**，业务代码不得自己拼路径。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storyforge.config import Settings, get_settings


class ArtifactNotFound(FileNotFoundError):
    """产物不存在。

    ★ 刻意**不用** `return ""` 或 `return None` ——
    旧项目 `read_chapter` 读不到时静默返回空串，导致**正文丢失不报错**。
    """


@dataclass(frozen=True)
class ArtifactRef:
    """产物引用 —— 图 state 里存的就是它。

    形如 `books/23/prose/00042.txt`（相对 `data/artifacts` 的 POSIX 风格路径）。
    """

    key: str

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    def __str__(self) -> str:
        return self.key


def content_hash(text: str) -> str:
    """内容指纹（sha256 前 32 位）—— 用于一致性校验与缓存判定。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class ArtifactStore:
    """文件型产物存储。

    所有方法都是**同步**的（文件 IO 快，且避免在节点里引入 async 复杂度）。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ArtifactStore:
        s = settings or get_settings()
        s.ensure_dirs()
        return cls(s.artifacts_dir)

    # -- 路径（唯一来源，禁止业务代码自行拼接）-----------------------------

    def _path(self, ref: ArtifactRef | str) -> Path:
        key = ref.key if isinstance(ref, ArtifactRef) else ref
        return self.root / key

    @staticmethod
    def prose_key(book_id: int, global_no: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/prose/{global_no:05d}.txt")

    @staticmethod
    def plan_key(book_id: int, global_no: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/plans/{global_no:05d}.json")

    @staticmethod
    def skeleton_key(book_id: int, volume_no: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/skeleton/v{volume_no:03d}.json")

    @staticmethod
    def review_key(book_id: int, global_no: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/reviews/{global_no:05d}.json")

    @staticmethod
    def extraction_key(book_id: int, global_no: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/extractions/{global_no:05d}.json")

    @staticmethod
    def bible_key(book_id: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/bible.md")

    @staticmethod
    def series_plan_key(book_id: int) -> ArtifactRef:
        return ArtifactRef(f"books/{book_id}/series_plan.json")

    # -- 文本 ---------------------------------------------------------------

    def write_text(self, ref: ArtifactRef | str, text: str) -> ArtifactRef:
        """写文本。父目录自动创建。"""
        r = ref if isinstance(ref, ArtifactRef) else ArtifactRef(ref)
        p = self._path(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再替换（防中断留下半截文件）
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
        return r

    def read_text(self, ref: ArtifactRef | str) -> str:
        """读文本。**不存在则抛 `ArtifactNotFound`**（不返回空串）。"""
        r = ref if isinstance(ref, ArtifactRef) else ArtifactRef(ref)
        p = self._path(r)
        if not p.exists():
            raise ArtifactNotFound(
                f"产物不存在：{r.key}（旧项目此处静默返回空串 → 正文丢失不报错）"
            )
        return p.read_text(encoding="utf-8")

    def exists(self, ref: ArtifactRef | str) -> bool:
        return self._path(ref).exists()

    def hash_of(self, ref: ArtifactRef | str) -> str | None:
        """内容指纹。不存在返回 `None`（缺省即不通过，不返回空串的 hash）。"""
        if not self.exists(ref):
            return None
        return content_hash(self.read_text(ref))

    # -- JSON ---------------------------------------------------------------

    def write_json(self, ref: ArtifactRef | str, data: Any) -> ArtifactRef:
        return self.write_text(
            ref, json.dumps(data, ensure_ascii=False, indent=1)
        )

    def read_json(self, ref: ArtifactRef | str) -> Any:
        """读 JSON。不存在或格式损坏都抛错。"""
        raw = self.read_text(ref)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArtifactNotFound(
                f"产物 JSON 损坏：{ref}（{exc}）—— 不静默降级为空 dict"
            ) from exc

    # -- 追加（正文续写）----------------------------------------------------

    def append_text(self, ref: ArtifactRef | str, chunk: str) -> ArtifactRef:
        """追加文本（用于正文续写）。

        旧项目 `rag.json` 整份重写导致 286 章约 **2.9 GB** 磁盘写；
        追加语义把重复写降为一次写。
        """
        r = ref if isinstance(ref, ArtifactRef) else ArtifactRef(ref)
        p = self._path(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(chunk)
        return r

    # -- 列举 ---------------------------------------------------------------

    def list_keys(self, prefix: str = "") -> list[str]:
        """列举产物 key（POSIX 风格，确定性排序）。"""
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and not p.name.endswith(".tmp")
        )

    def delete(self, ref: ArtifactRef | str) -> bool:
        p = self._path(ref)
        if p.exists():
            p.unlink()
            return True
        return False

    def book_size_bytes(self, book_id: int) -> int:
        """整本书的产物大小（用于监控 checkpoint/存储膨胀）。"""
        base = self.root / "books" / str(book_id)
        if not base.exists():
            return 0
        return sum(p.stat().st_size for p in base.rglob("*") if p.is_file())


__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactNotFound",
    "content_hash",
]
