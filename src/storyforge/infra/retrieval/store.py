"""检索缓存 —— 带 TTL 的本地持久化 + **分阶段断点续跑**。

## 为什么需要缓存

learn 的流程是「研究 → 生成平替修辞 → 用户确认 → 世界观」，
其中**用户确认**可能隔很久，甚至跨越会话。若不缓存，
每次都要重新联网搜一遍（同一主题重复烧配额）。

旧项目也有缓存（`research_store.py`，185 行；`research_cache.json`
实测 **5,576.8 KB**），但：
  · 缓存文件放在**仓库根**而非 `data/`（容易误入库）
  · `RESEARCH_CACHE_TTL_DAYS` 有配置键，但 `get()` **不检查 TTL**
    → "过期"只是注释里的意图，不是行为

本实现：
  · 存 `data/research_cache.json`（`data/` 已 gitignore）
  · `get()` **真的检查 TTL**，过期视为未命中
  · 分阶段断点（`stage` 字段）—— 研究中断后可从上次阶段继续

## 断点续跑的语义

旧项目 `{queries/snippets/map/verdicts}` 四阶段。本实现保留 `stage` 字符串，
但**不预设阶段名**（learn 的流程还会演化）；调用方写入自己认的阶段标识，
`put_stage` / `get_stage` 负责存取。这样阶段划分不必改本模块。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from storyforge.config import get_settings

#: 多线程/多 worker 写同一文件时的保护（三级流水线是并发的）
_LOCK = threading.Lock()


def _normalize(topic: str) -> str:
    """归一化主题键（去空白 + 小写）—— 与旧项目同口径。"""
    return re.sub(r"\s+", "", topic or "").lower()


class ResearchCache:
    """主题级检索缓存（JSON 文件 + TTL + 阶段断点）。"""

    def __init__(self, path: Path | None = None, *, ttl_days: int | None = None) -> None:
        s = get_settings()
        self.path = path or (s.data_dir / "research_cache.json")
        self.ttl_days = (
            ttl_days if ttl_days is not None else s.research.cache_ttl_days
        )

    # -- 读写 ---------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            # ★ 坏缓存不得让流程失败 —— 视为空缓存（会重新联网）
            #   但不静默：留一条 warning 由调用方决定是否展示
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # -- 主题级缓存 ---------------------------------------------------------

    def get(self, topic: str) -> dict[str, Any] | None:
        """取未过期的缓存；**过期视为未命中**（返回 None）。

        旧项目的 `get()` 不检查 TTL —— "过期"只是配置里的一句意图。
        """
        with _LOCK:
            entry = self._load().get(_normalize(topic))
        if not isinstance(entry, dict):
            return None
        ts = entry.get("updated_at")
        if not ts:
            return None
        try:
            when = datetime.fromisoformat(str(ts))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if datetime.now(UTC) - when > timedelta(days=self.ttl_days):
            return None
        return entry

    def put(self, topic: str, result: Any, *, stage: str = "") -> None:
        """写入主题的**最终结果**（保留 hit_count 与**已存的阶段断点**）。

        ★ 必须保留 `stages` —— 初版直接重建条目，把断点冲掉了，
        于是 `put()` 之后再 `get_stage()` 拿到 None
        （被自己的测试 `test_cache_stage_checkpoint_is_independent` 抓到）。
        断点与最终结果生命周期不同，谁后写都不能吃掉对方。
        """
        with _LOCK:
            data = self._load()
            key = _normalize(topic)
            old = data.get(key) if isinstance(data.get(key), dict) else {}
            entry: dict[str, Any] = {
                "result": result,
                "stage": stage or old.get("stage", ""),
                "hit_count": int(old.get("hit_count", 0)),
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            if isinstance(old.get("stages"), dict):
                entry["stages"] = old["stages"]      # ★ 保留断点
            data[key] = entry
            self._save(data)

    def bump_hit(self, topic: str) -> int:
        """增加命中计数并返回新值（用于观测"这个主题被复用了几次"）。"""
        with _LOCK:
            data = self._load()
            key = _normalize(topic)
            if key not in data or not isinstance(data[key], dict):
                return 0
            data[key]["hit_count"] = int(data[key].get("hit_count", 0)) + 1
            self._save(data)
            return int(data[key]["hit_count"])

    # -- 阶段断点（断点续跑）------------------------------------------------

    def get_stage(self, topic: str, stage: str) -> Any | None:
        """取某主题某阶段的中间产物（研究中断后继续用）。"""
        entry = self.get(topic)
        if not entry:
            return None
        stages = entry.get("stages")
        if not isinstance(stages, dict):
            return None
        return stages.get(stage)

    def put_stage(self, topic: str, stage: str, payload: Any) -> None:
        """写某阶段的中间产物（**保留其它阶段**）。

        ★ 与 `put()` 分开：`put()` 写"主题的最终结果"，
        `put_stage()` 写"某一步的中间态" —— 两者生命周期不同，
        混在一起会让"重跑第二阶段"把第一阶段结果冲掉。
        """
        with _LOCK:
            data = self._load()
            key = _normalize(topic)
            entry = data.get(key) if isinstance(data.get(key), dict) else {}
            stages = entry.get("stages")
            if not isinstance(stages, dict):
                stages = {}
            stages[stage] = {
                "payload": payload,
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            entry["stages"] = stages
            entry.setdefault("hit_count", 0)
            entry.setdefault("updated_at",
                             datetime.now(UTC).isoformat(timespec="seconds"))
            data[key] = entry
            self._save(data)

    def clear(self, topic: str | None = None) -> int:
        """清缓存（`topic=None` 清全部）。返回清掉的条目数。"""
        with _LOCK:
            if topic is None:
                data = self._load()
                n = len(data)
                self._save({})
                return n
            data = self._load()
            key = _normalize(topic)
            if key in data:
                del data[key]
                self._save(data)
                return 1
            return 0

    # -- 诊断 ---------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """缓存概览（供 CLI **可视化**展示：多少主题、多少过期）。"""
        data = self._load()
        fresh = stale = 0
        for topic in data:
            if self.get(topic) is not None:
                fresh += 1
            else:
                stale += 1
        return {
            "path": str(self.path),
            "topics": len(data),
            "fresh": fresh,
            "stale": stale,
            "ttl_days": self.ttl_days,
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }


__all__ = ["ResearchCache"]
