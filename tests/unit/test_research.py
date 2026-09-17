"""联网研究（阶段 2-2）测试 —— 全部**离线**，不打网络。

## 本文件防的是什么

learn 的目标流程要求「联网搜索知识点（**按篇幅需要动态调整**，
一次不够后续动态补充）」。这句话有两个必须落地的判据：

  ① **停** —— 什么时候算够？
  ② **续** —— 什么时候该再搜一轮？

若两个判据没有测试钉住，每个调用点都会自己拍一个轮次数（旧项目形态），
口径必然漂移 —— 要么知识点不够就开写（正文没料可讲），
要么无限搜（烧配额）。

## 为什么用假 provider 而不是真 Tavily

真联网测试有三个不可接受的性质：**慢、花钱、结果不稳定**。
本文件的判据全是"编排逻辑"（轮次/去重/达标/缓存），
与"检索质量"无关 —— 后者属 2-3 学习地图的职责。
真实链路单独由 `scripts/verify_research_offline.py` + 手动联调覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyforge.config import ResearchSettings
from storyforge.infra.retrieval import (
    ANGLES,
    MockResearchProvider,
    ResearchCache,
    ResearchError,
    TavilyResearchProvider,
    estimate_points,
    make_research_provider,
    research_topic,
    target_points_for,
)
from storyforge.modes.protocols import ResearchSnippet

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 假 provider（可控条数与长度）
# --------------------------------------------------------------------------- #


class FakeProvider:
    """每轮返回固定条数的**长文本**（够长 → 计入知识点）。"""

    def __init__(self, per_round: int = 4, *, text_len: int = 200,
                 fail_rounds: set[int] | None = None) -> None:
        self.per_round = per_round
        self.text_len = text_len
        self.fail_rounds = fail_rounds or set()
        self.calls = 0
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5) -> list[ResearchSnippet]:
        self.calls += 1
        self.queries.append(query)
        if self.calls in self.fail_rounds:
            raise ResearchError(f"模拟第 {self.calls} 轮失败")
        return [
            ResearchSnippet(
                title=f"R{self.calls}-{i}",
                # ★ url 唯一 —— 否则会被去重逻辑当重复项
                url=f"https://example.com/{self.calls}/{i}",
                text="内容" * (self.text_len // 2),
            )
            for i in range(self.per_round)
        ]


class RepeatingProvider(FakeProvider):
    """所有轮次返回**相同 URL**（测去重）。"""

    def search(self, query: str, max_results: int = 5) -> list[ResearchSnippet]:
        self.calls += 1
        self.queries.append(query)
        return [
            ResearchSnippet(title=f"同一条{i}", url=f"https://same.com/{i}",
                            text="内容" * 100)
            for i in range(4)
        ]


@pytest.fixture
def cache(tmp_path: Path) -> ResearchCache:
    return ResearchCache(path=tmp_path / "rc.json")


@pytest.fixture
def settings() -> ResearchSettings:
    """小目标 + 少轮次，让测试快且判据明确。"""
    return ResearchSettings(
        points_per_scale={"fast": 4, "standard": 8, "grand": 20, "ultra": 40},
        max_rounds=4,
        max_results_per_query=4,
    )


# --------------------------------------------------------------------------- #
# 目标知识点（"停"的判据）
# --------------------------------------------------------------------------- #


def test_target_points_by_scale() -> None:
    """★ 各档位的目标知识点数必须与 `ResearchSettings` 一致。

    这是"停"的判据来源：搜到这个数就够了。
    """
    s = ResearchSettings()
    assert target_points_for("fast", s) == s.points_per_scale["fast"]
    assert target_points_for("standard", s) == s.points_per_scale["standard"]
    assert target_points_for("grand", s) == s.points_per_scale["grand"]
    assert target_points_for("ultra", s) == s.points_per_scale["ultra"]


def test_target_points_unknown_scale_falls_back_not_zero() -> None:
    """★ 未知档位**不得回落为 0**。

    取 0 会让研究直接"达标" → 正文无料可讲。
    回落为 standard 是安全的（多搜总比没料好）。
    """
    s = ResearchSettings()
    got = target_points_for("不存在的档位", s)
    assert got == s.points_per_scale["standard"] > 0


def test_target_points_monotonic_by_scale() -> None:
    """篇幅越大，目标知识点越多（单调）—— 否则"按篇幅调整"名不副实。"""
    s = ResearchSettings()
    vals = [target_points_for(x, s) for x in ("fast", "standard", "grand", "ultra")]
    assert vals == sorted(vals), f"目标知识点未随篇幅单调递增：{vals}"
    assert len(set(vals)) == len(vals), "各档位应有区分度"


# --------------------------------------------------------------------------- #
# 知识点估算
# --------------------------------------------------------------------------- #


def test_estimate_points_ignores_short_snippets() -> None:
    """★ 过短的片段不计入（导航页/摘要页信息量不足）。

    否则"条数够了"会伪装成"知识点够了"，然后正文没料可讲。
    """
    short = ResearchSnippet(title="t", url="u1", text="很短")
    long = ResearchSnippet(title="t", url="u2", text="内容" * 100)
    assert estimate_points([short]) == 0
    assert estimate_points([long]) > 0
    assert estimate_points([short, long]) == estimate_points([long])


def test_estimate_points_is_conservative() -> None:
    """★ 估算刻意保守（低估）—— 高估的代价更大。

    低估 → 多搜几轮（多花配额）；
    高估 → 知识点不够就开写（正文没料）。

    ⚠️ 但"保守"不等于"归零"：初版用 `int(条数 × 0.5)` 被本文件的
    `test_estimate_points_ignores_short_snippets` 当场抓到 ——
    **单个有效片段算出 0 个知识点**，那是错的。
    """
    from storyforge.infra.retrieval.research import SNIPPETS_PER_POINT

    assert SNIPPETS_PER_POINT >= 2, "一条检索结果不足以支撑一个完整知识点"
    long = [ResearchSnippet(title="t", url=f"u{i}", text="内容" * 100)
            for i in range(10)]
    assert estimate_points(long) < len(long)


def test_estimate_points_never_zero_for_useful_content() -> None:
    """★★ 只要有**一条**有效片段，估算就必须 ≥1（不得归零）。

    这是初版真实缺陷的回归测试：`int(1 * 0.5) == 0` 会让
    "搜到东西了"与"什么都没搜到"在判据上**无法区分** →
    永远不达标 → 无限搜到轮次上限。
    """
    one = ResearchSnippet(title="t", url="u", text="内容" * 50)
    assert estimate_points([one]) >= 1, (
        "单条有效片段被算成 0 个知识点 —— 判据会误判为'没搜到'"
    )
    # 单调性：条数增加，估算不会下降
    seq = [ResearchSnippet(title="t", url=f"u{i}", text="内容" * 50)
           for i in range(6)]
    vals = [estimate_points(seq[:k]) for k in range(1, 7)]
    assert vals == sorted(vals), f"估算不单调：{vals}"


# --------------------------------------------------------------------------- #
# 轮次由目标驱动（"续"的判据）
# --------------------------------------------------------------------------- #


def test_rounds_stop_exactly_when_target_reached(cache, settings) -> None:
    """★★ **达标即停** —— 这是"按篇幅动态调整"的核心。

    每轮 4 条 × 0.5 = 2 知识点；目标 8 → 恰好 **4 轮**。
    少搜 = 没达标却提前停；多搜 = 达标后还在烧配额。
    """
    p = FakeProvider(per_round=4)
    plan = research_topic("主题", scale="standard", provider=p,
                          cache=cache, settings=settings)
    assert plan.target_points == 8
    assert plan.reached_target, f"应达标，实得 ≈{plan.estimated_points}"
    assert p.calls == 4, f"应恰好 4 轮，实得 {p.calls}"
    assert plan.rounds_used == 4


def test_smaller_scale_searches_fewer_rounds(cache, settings) -> None:
    """★ 篇幅越小，轮次越少 —— "按篇幅调整"的直接体现。"""
    small = FakeProvider(per_round=4)
    big = FakeProvider(per_round=4)
    plan_small = research_topic("主题", scale="fast", provider=small,
                                cache=cache, settings=settings)
    cache.clear()
    plan_big = research_topic("主题", scale="grand", provider=big,
                              cache=cache, settings=settings)
    assert small.calls < big.calls, (
        f"fast({small.calls} 轮) 应少于 grand({big.calls} 轮)"
    )
    assert plan_small.target_points < plan_big.target_points


def test_unreached_target_is_reported_not_faked(cache, settings) -> None:
    """★★ 搜不够时必须**如实报告缺口**，不得假装成功。

    判据：`reached_target=False` + `stopped_reason` 说明原因。
    旧项目反复出现的形态是"缺省即通过"——这里必须相反。
    """
    p = FakeProvider(per_round=4, text_len=10)   # 全是不合格短文本
    plan = research_topic("主题", scale="standard", provider=p,
                          cache=cache, settings=settings)
    assert plan.reached_target is False
    assert plan.estimated_points < plan.target_points
    assert plan.stopped_reason, "未达标必须说明原因"


def test_each_round_uses_different_angle(cache, settings) -> None:
    """★ 每轮换**检索角度** —— 同一 query 重复搜只会拿到同样结果。

    且角度顺序是教学的自然顺序（先"是什么"再"怎么来的"）。
    """
    p = FakeProvider(per_round=4, text_len=10)   # 逼它用满轮次
    research_topic("点茶", scale="grand", provider=p, cache=cache, settings=settings)
    assert len(set(p.queries)) == len(p.queries), "出现了重复 query"
    assert p.queries[0] == ANGLES[0].format(topic="点茶")
    assert "核心概念" in p.queries[0]
    assert "发展历史" in p.queries[1]


def test_duplicate_urls_are_deduplicated(cache, settings) -> None:
    """★ 同一 URL 不重复计入（同一文章在多轮里反复出现是常态）。

    不去重会让"累计知识点"虚高 → 提前停 → 料不够。
    """
    p = RepeatingProvider()
    plan = research_topic("主题", scale="standard", provider=p,
                          cache=cache, settings=settings)
    # 4 条唯一 URL → 2 知识点（< 目标 8）→ 永不达标
    assert plan.reached_target is False
    assert plan.total_snippets == 4, (
        f"重复 URL 未被去重，实得 {plan.total_snippets} 条"
    )


def test_single_round_failure_does_not_abort(cache, settings) -> None:
    """★ 单轮失败**不中断整体**（网络抖动不该让整个研究失败）。

    失败记在 `ResearchRound.error` 里，后续轮次继续。
    """
    p = FakeProvider(per_round=4, fail_rounds={2})
    plan = research_topic("主题", scale="standard", provider=p,
                          cache=cache, settings=settings)
    failed = [r for r in plan.rounds if r.error]
    assert len(failed) == 1, "应记录恰好一轮失败"
    assert failed[0].round_no == 2
    # 其余轮次仍成功（含失败之后的第 3、4 轮）
    ok_rounds = [r for r in plan.rounds if not r.error]
    assert len(ok_rounds) >= 3


def test_all_rounds_failing_reports_reason(cache, settings) -> None:
    """全部轮次失败 → 未达标 + 原因里说明有几轮失败。"""
    p = FakeProvider(per_round=4, fail_rounds={1, 2, 3, 4})
    plan = research_topic("主题", scale="standard", provider=p,
                          cache=cache, settings=settings)
    assert plan.reached_target is False
    assert "失败" in plan.stopped_reason


def test_max_rounds_is_respected(cache, settings) -> None:
    """轮次上限必须生效（防无限搜烧配额）。"""
    p = FakeProvider(per_round=1, text_len=10)     # 永远不达标
    plan = research_topic("主题", scale="ultra", provider=p,
                          cache=cache, settings=settings)
    assert p.calls <= settings.max_rounds
    assert plan.rounds_used <= settings.max_rounds


# --------------------------------------------------------------------------- #
# 缓存与断点续跑
# --------------------------------------------------------------------------- #


def test_cache_hit_skips_network(cache, settings) -> None:
    """★★ 缓存命中时**不再联网**（用户确认可能隔很久，重复搜是浪费）。"""
    p1 = FakeProvider(per_round=4)
    research_topic("主题", scale="standard", provider=p1, cache=cache,
                   settings=settings)
    assert p1.calls > 0

    p2 = FakeProvider(per_round=4)
    plan2 = research_topic("主题", scale="standard", provider=p2, cache=cache,
                           settings=settings)
    assert p2.calls == 0, "缓存命中后不应再调 provider"
    assert plan2.rounds[0].from_cache is True
    assert plan2.reached_target is True


def test_cache_ttl_expiry_is_real(tmp_path, settings) -> None:
    """★ TTL 必须**真的生效** —— 旧项目有 `RESEARCH_CACHE_TTL_DAYS`
    配置键但 `get()` 不检查，过期只是注释里的意图。

    这里把 TTL 设为 0 天，写入后立即视为过期。
    """
    c = ResearchCache(path=tmp_path / "rc.json", ttl_days=0)
    c.put("主题", {"snippets": []})
    # ttl=0 → 任何已写入的条目都过期（`now - updated_at > 0`）
    import time
    time.sleep(0.01)
    assert c.get("主题") is None, "过期条目必须视为未命中"


def test_cache_fresh_entry_survives(tmp_path) -> None:
    """未过期条目必须能读到（否则缓存形同虚设）。"""
    c = ResearchCache(path=tmp_path / "rc.json", ttl_days=30)
    c.put("主题", {"snippets": [{"title": "t", "url": "u", "text": "x"}]})
    assert c.get("主题") is not None


def test_cache_normalizes_topic_key(tmp_path) -> None:
    """主题键归一化（去空白 + 小写）—— 与旧项目同口径。

    否则「宋代 点茶」与「宋代点茶」会各存一份。
    """
    c = ResearchCache(path=tmp_path / "rc.json")
    c.put("宋代 点茶", {"snippets": []})
    assert c.get("宋代点茶") is not None
    assert c.get("  宋代点茶  ") is not None


def test_cache_corrupt_file_does_not_crash(tmp_path) -> None:
    """★ 坏缓存不得让流程失败 —— 视为空缓存（会重新联网），不抛错。"""
    p = tmp_path / "rc.json"
    p.write_text("{不是合法 JSON", encoding="utf-8")
    c = ResearchCache(path=p)
    assert c.get("主题") is None
    c.put("主题", {"snippets": []})        # 覆盖坏文件
    assert c.get("主题") is not None


def test_cache_stage_checkpoint_is_independent(tmp_path) -> None:
    """★ 阶段断点与主题结果**互不覆盖**（断点续跑的基础）。

    `put()` 写"最终结果"，`put_stage()` 写"某步中间态"——
    生命周期不同，混在一起会让"重跑第二阶段"冲掉第一阶段结果。
    """
    c = ResearchCache(path=tmp_path / "rc.json")
    c.put("主题", {"snippets": [{"title": "final", "url": "u", "text": "x"}]})
    c.put_stage("主题", "queries", ["q1", "q2"])
    c.put_stage("主题", "snippets", ["s1"])
    c.put("主题", {"snippets": [{"title": "final2", "url": "u2", "text": "y"}]})

    assert c.get_stage("主题", "queries")["payload"] == ["q1", "q2"]
    assert c.get_stage("主题", "snippets")["payload"] == ["s1"]
    assert c.get("主题")["result"]["snippets"][0]["title"] == "final2"


def test_cache_hit_count_and_stats(tmp_path) -> None:
    """缓存统计供 CLI **可视化**（用户要求"避免黑盒"）。"""
    c = ResearchCache(path=tmp_path / "rc.json", ttl_days=30)
    c.put("a", {"snippets": []})
    c.put("b", {"snippets": []})
    assert c.bump_hit("a") == 1
    assert c.bump_hit("a") == 2
    assert c.bump_hit("不存在") == 0

    st = c.stats()
    assert st["topics"] == 2
    assert st["fresh"] == 2
    assert st["stale"] == 0
    assert st["ttl_days"] == 30
    assert st["size_bytes"] > 0
    assert json.dumps(st)          # 可序列化（CLI 要打印）


# --------------------------------------------------------------------------- #
# provider 选择与 Mock 可识别性
# --------------------------------------------------------------------------- #


def test_mock_results_are_identifiable() -> None:
    """★★ Mock 结果必须**可识别** —— 下游能据此拒绝把占位文本当知识来源。

    旧项目 Mock 返回纯提示文本、**无结构化标记**，容易被误当真结果。
    """
    sn = MockResearchProvider().search("主题")[0]
    assert sn.title.startswith("[Mock]")
    assert sn.url == "", "Mock 结果无 URL（不可溯源，应能被识别）"
    assert "不是真实检索内容" in sn.text


def test_make_provider_returns_mock_without_key(monkeypatch) -> None:
    """无 key → Mock（且**允许**，以便离线跑通全链路）。"""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    p = make_research_provider()
    assert isinstance(p, MockResearchProvider)


def test_make_provider_refuses_mock_when_disallowed(monkeypatch) -> None:
    """★ `allow_mock=False` 时无 key 必须**抛错**（最终验收要真联网）。"""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ResearchError, match="未配置"):
        make_research_provider(allow_mock=False)


def test_make_provider_returns_tavily_with_key(monkeypatch) -> None:
    """有 key → Tavily（**显式构造**，不是"运行到某处静默降级"）。"""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-test")
    p = make_research_provider()
    assert isinstance(p, TavilyResearchProvider)


def test_tavily_provider_rejects_empty_key() -> None:
    """空 key 必须抛错（避免发出必然 401 的请求）。"""
    with pytest.raises(ResearchError, match="需要 api_key"):
        TavilyResearchProvider("")


# --------------------------------------------------------------------------- #
# 可视化（用户要求"全流程可视化"）
# --------------------------------------------------------------------------- #


def test_summary_lines_show_every_round(cache, settings) -> None:
    """★ 逐轮可视化：每轮的 query、条数、累计知识点都要能看到。

    这是"避免黑盒"的落点 —— 用户要能看懂系统搜了什么、够不够。
    """
    p = FakeProvider(per_round=4)
    plan = research_topic("点茶", scale="standard", provider=p,
                          cache=cache, settings=settings)
    text = "\n".join(plan.summary_lines())
    assert "点茶" in text
    assert "目标知识点：8" in text
    for r in plan.rounds:
        assert r.query in text
    assert "达到目标" in text


def test_summary_lines_show_shortfall(cache, settings) -> None:
    """未达标时可视化文本必须**说明缺口**（不能只显示"完成"）。"""
    p = FakeProvider(per_round=4, text_len=10)
    plan = research_topic("点茶", scale="standard", provider=p,
                          cache=cache, settings=settings)
    text = "\n".join(plan.summary_lines())
    assert "未达目标" in text
    assert plan.stopped_reason in text
