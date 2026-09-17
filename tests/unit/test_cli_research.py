"""`storyforge research` 命令测试 —— learn 的**研究可视化**入口。

## 为什么这条命令值得测试

用户要求 learn「全流程**可视化**，避免黑盒」。
研究是流程里**最不可见**的一步：若不展示"搜了什么、够不够"，
用户只能看到最终正文，无法判断知识点是否充分。

本文件断言的是**用户实际会看到的输出**，不是内部对象。

⚠️ 全部离线（Mock provider 或假 provider），不打网络、不花钱。
"""

from __future__ import annotations

import pytest

from storyforge.cli import build_parser, cmd_research

pytestmark = pytest.mark.unit


class _Args:
    """极简 args（`cmd_research` 只读这几个属性）。"""

    def __init__(self, topic=None, scale="standard", show_snippets=0,
                 no_cache=False, require_real=False, cache_stats=False) -> None:
        self.topic = topic
        self.scale = scale
        self.show_snippets = show_snippets
        self.no_cache = no_cache
        self.require_real = require_real
        self.cache_stats = cache_stats


def test_cli_has_research_command() -> None:
    p = build_parser()
    ns = p.parse_args(["research"])
    assert getattr(ns, "func", None) is cmd_research


def test_research_requires_topic(capsys) -> None:
    """无 `--topic` 且非 `--cache-stats` → 退出码 2 + 明确提示。"""
    rc = cmd_research(_Args())
    assert rc == 2
    assert "--topic" in capsys.readouterr().err


def test_research_cache_stats_is_offline_and_free(capsys) -> None:
    """★ `--cache-stats` 必须**零 LLM、零网络**（只读本地缓存文件）。

    防的是"看个缓存统计也要联网/要 key"。
    """
    rc = cmd_research(_Args(cache_stats=True))
    out = capsys.readouterr().out
    assert rc == 0
    for label in ("path", "topics", "fresh", "stale", "ttl_days", "size_bytes"):
        assert label in out, f"缓存统计缺字段 {label}"


def test_research_shows_target_points_by_scale(capsys) -> None:
    """★ 必须显示**目标知识点数** —— 它是"停/续"判据，用户要能核对。

    不同档位的目标必须不同（否则"按篇幅调整"不可见）。
    """
    rc = cmd_research(_Args(topic="宋代点茶", scale="fast"))
    out = capsys.readouterr().out
    assert "目标知识点" in out
    assert "fast" in out
    # fast 档的目标应当在输出里具体可见
    from storyforge.infra.retrieval import target_points_for
    assert str(target_points_for("fast")) in out


def test_research_shows_every_round_query(capsys) -> None:
    """★★ 逐轮可视化：每轮的 query 都要能看到。

    这是"避免黑盒"的核心 —— 用户要能看出系统换了几种角度搜。
    """
    rc = cmd_research(_Args(topic="一个足够特别的主题", scale="fast"))
    out = capsys.readouterr().out
    assert "第1轮" in out
    assert "核心概念" in out, "第一轮的角度应可见"


def test_research_reports_shortfall_honestly(capsys) -> None:
    """★★ 未达标必须**如实报告**，不得只显示"完成"。

    Mock 的占位文本很短 → 估算 0 知识点 → 必然未达标。
    这正是要展示给用户的诚实结论（而不是假装成功）。
    """
    rc = cmd_research(_Args(topic="另一个主题", scale="ultra"))
    out = capsys.readouterr().out
    assert rc == 1, "未达标应返回非零退出码"
    assert "未达目标" in out
    assert "角度用尽" in out or "轮次" in out


def test_research_warns_loudly_in_mock_mode(capsys) -> None:
    """★★ Mock 模式必须**显眼告警**，且片段带 `[Mock]` 标记。

    否则用户可能把占位文本当真实检索结果 —— 旧项目 Mock 无结构化标记，
    容易被误认（本项目已记录：`没有任何信号` 与 `信号为 0` 是两回事）。
    """
    rc = cmd_research(_Args(topic="第三个主题", scale="fast", show_snippets=1))
    out = capsys.readouterr().out
    assert "Mock" in out
    assert "占位" in out
    assert "[Mock]" in out, "片段必须带可识别标记"


def test_research_require_real_fails_without_key(capsys, monkeypatch) -> None:
    """`--require-real` 无 key 时必须**失败**（不静默降级为 Mock）。"""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    rc = cmd_research(_Args(topic="主题", require_real=True))
    assert rc == 1
    assert "TAVILY_API_KEY" in capsys.readouterr().err
