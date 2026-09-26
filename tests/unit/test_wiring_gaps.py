"""四个"设计了但没接线"断点的回归测试（S0 后集中修复）。

## 本文件防的是什么

这四个断点形状完全相同：**代码写好了、测试也绿，但生产里没人调用** ——
所以它们**不会报错**，只会让功能静默地不存在。

| # | 断点 | 症状 |
|---|---|---|
| A | 超时 < 输出上限所需时间 | `cap` 物理不可达，长章永远失败 |
| B | `settings.toml` 无 `toml_file` | 改配置**零效果** |
| C | `record_to_row` 零生产调用点 | `llm_calls` 永远 0 行，费用跨运行查不到 |
| D | `open_checkpointer` 只被测试调用 | 断点恢复能力在生产**从未成立** |

★ 为什么每个都要"证明它真的生效"而不是"函数存在"：
本项目已栽过三次「检查器看着在测、实际恒真」（规则 3、规则 22、`validate_map`）。
故下面每个测试都断言**可观测的结果**（DB 行数 / 文件产生 / 状态变化），
不断言"函数被调用了"。
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from storyforge.infra.llm.client import ClientConfig, LLMClient

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# A. 超时 与 输出上限必须相容
# --------------------------------------------------------------------------- #


def test_output_cap_fits_in_timeout() -> None:
    """★ `cap` 必须在超时内可达（详见 test_batch_budget.py 的推演）。"""
    from storyforge.config import get_settings

    cap = ClientConfig().max_output_tokens_cap
    timeout = get_settings().llm.default_timeout_s
    # 实测 13.7 tok/s
    assert cap / 13.7 <= timeout, "输出上限在超时内不可达"


def test_llm_settings_and_client_config_defaults_agree() -> None:
    """★ 两处超时默认值必须一致 —— 否则"改了配置没生效"。

    `LLMSettings.default_timeout_s`（经 CLI 注入 `ClientConfig`）
    与 `ClientConfig.timeout_s`（直接构造时用）是**两个默认值**，
    分歧时行为随构造路径而变。
    """
    from storyforge.config import get_settings

    assert ClientConfig().timeout_s == get_settings().llm.default_timeout_s, (
        "ClientConfig.timeout_s 与 LLMSettings.default_timeout_s 不一致"
    )


# --------------------------------------------------------------------------- #
# C. llm_calls 真的落库
# --------------------------------------------------------------------------- #


def _client_with_one_call():
    async def transport(**_kw):
        return "正文", {"prompt_tokens": 1000, "completion_tokens": 500,
                        "finish_reason": "stop"}

    c = LLMClient(transport, config=ClientConfig(max_attempts=1, backoff_base_s=0.0))
    asyncio.run(c.complete(node="chapter_writer", system="s", user="u"))
    return c


def test_persist_calls_actually_writes_rows(db, tmp_settings) -> None:
    """★ 调用记录必须真的进 `llm_calls` 表（修复前恒 0 行）。"""
    from storyforge.observability.report import persist_calls

    client = _client_with_one_call()
    written = persist_calls(client.records, db)
    assert written == 1

    con = sqlite3.connect(tmp_settings.db_path)
    row = con.execute(
        "SELECT node_name, model, prompt_tokens, completion_tokens, status FROM llm_calls"
    ).fetchone()
    assert row is not None, "llm_calls 表仍是空的 —— 观测链路又断了"
    assert row[0] == "chapter_writer"
    assert row[2] == 1000 and row[3] == 500


def test_persist_calls_is_idempotent(db) -> None:
    """同一批记录重复落库不得重复插入（重放/重跑场景）。"""
    from storyforge.observability.report import persist_calls

    client = _client_with_one_call()
    assert persist_calls(client.records, db) == 1
    assert persist_calls(client.records, db) == 0, "重复写入了同一 call_id"


def test_cli_persists_calls_and_reports_count(db, tmp_settings, capsys) -> None:
    """★ CLI 收口点必须真的调用落库（不是只定义函数）。"""
    from storyforge.cli import _persist_calls

    client = _client_with_one_call()
    _persist_calls(client)

    con = sqlite3.connect(tmp_settings.db_path)
    assert con.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1
    assert "已入库" in capsys.readouterr().out


def test_cli_respects_persist_calls_off(db, tmp_settings, capsys, monkeypatch) -> None:
    """`persist_calls=false` 时**不落库但要说出来**（不静默）。"""
    from storyforge.cli import _persist_calls
    from storyforge.config import get_settings

    monkeypatch.setattr(
        type(get_settings().observability), "persist_calls",
        property(lambda self: False), raising=False,
    )
    client = _client_with_one_call()
    _persist_calls(client)

    con = sqlite3.connect(tmp_settings.db_path)
    assert con.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
    assert "persist_calls=false" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# D. checkpointer 真的被挂到图上
# --------------------------------------------------------------------------- #


def test_graphs_receive_injected_checkpointer(db, tmp_settings) -> None:
    """★ 五个 `compile()` 都必须接收注入的 checkpointer。

    修复前它们**全都不传** —— 于是"崩溃恢复后已完成单元不重跑"（不变量 4）
    在生产中从未成立。这里用"图对象的 checkpointer 非空"来证明接线存在。
    """
    from storyforge.graph.graphs.books import GraphDeps, open_checkpointer
    from storyforge.graph.graphs.books import (
        build_bible_graph,
        build_detail_batch_graph,
        build_skeleton_graph,
        build_volume_plan_graph,
    )
    from storyforge.infra.storage.artifacts import ArtifactStore

    store = ArtifactStore.from_settings()

    async def _check() -> None:
        async with open_checkpointer() as saver:
            deps = GraphDeps(client=None, store=store, checkpointer=saver)
            for build in (build_bible_graph, build_volume_plan_graph,
                          build_skeleton_graph, build_detail_batch_graph):
                g = build(deps)
                assert g.checkpointer is not None, (
                    f"{build.__name__} 没有挂 checkpointer —— 恢复能力又断了"
                )

    asyncio.run(_check())


def test_chapter_graph_receives_injected_checkpointer(db, tmp_settings) -> None:
    """单章图同理（它是断点恢复最需要的那张图）。"""
    from storyforge.graph.graphs.books import open_checkpointer
    from storyforge.graph.graphs.chapter import ChapterDeps, build_chapter_graph
    from storyforge.infra.storage.artifacts import ArtifactStore

    store = ArtifactStore.from_settings()

    async def _check() -> None:
        async with open_checkpointer() as saver:
            deps = ChapterDeps(client=None, store=store, checkpointer=saver)
            assert build_chapter_graph(deps).checkpointer is not None

    asyncio.run(_check())


def test_open_checkpointer_creates_its_db_file(db, tmp_settings) -> None:
    """`open_checkpointer` 必须真的建出 DB —— 修复前 `data/checkpoints.db` 从未出现。"""
    from storyforge.graph.graphs.books import open_checkpointer

    path = tmp_settings.checkpoint_db_path
    assert not path.exists()

    async def _touch() -> None:
        async with open_checkpointer():
            pass

    asyncio.run(_touch())
    assert path.exists(), "checkpointer 没有落盘 —— 恢复能力不存在"


async def test_invoke_resumable_uses_full_input_on_fresh_thread() -> None:
    """无 checkpoint 时必须传完整输入（否则 LangGraph 抛 EmptyInputError）。"""
    from storyforge.cli import _invoke_resumable

    calls: list[str] = []

    class _FakeGraph:
        @staticmethod
        async def aget_state(cfg):
            class S:
                next = ()          # 空 = 无未完成工作 → 必须传完整输入
            return S()

        @staticmethod
        async def ainvoke(payload, cfg):
            calls.append("full" if payload is not None else "resume")
            return {"ok": True}

    out = await _invoke_resumable(
        _FakeGraph(), {"book_id": 1}, {"configurable": {"thread_id": "t:fresh"}})
    assert out == {"ok": True}
    assert calls == ["full"], "新 thread 应传完整输入"


def test_invoke_resumable_passes_none_when_work_pending() -> None:
    """★ thread 有未完成工作时必须传 `None`（真 resume），而不是重跑。

    实测：`ainvoke(完整输入)` 会**从 START 重跑**，即使已有 checkpoint ——
    CLI 此前永远传完整 dict，于是 checkpointer 的意义被完全抵消。
    """
    from storyforge.cli import _invoke_resumable

    calls: list[str] = []

    class _GraphWithPending:
        async def aget_state(self, cfg):
            class S:
                next = ("write",)
            return S()

        async def ainvoke(self, payload, cfg):
            calls.append("resume" if payload is None else "full")
            return {"ok": True}

    out = asyncio.run(_invoke_resumable(_GraphWithPending(), {"book_id": 1},
                                       {"configurable": {"thread_id": "t"}}))
    assert out == {"ok": True}
    assert calls == ["resume"], "有未完成断点却重跑了 —— 恢复语义被破坏"
