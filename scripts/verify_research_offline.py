"""2-2 联网研究的**离线**冒烟验证（不联网、不花钱）。

用一个"假 Tavily"（每轮返回 4 条长文本）验证：

  ① 轮次由**知识点目标**驱动（不是固定轮数）
  ② 达标即停（不多搜）
  ③ 断点续跑：第二次同主题直接命中缓存
  ④ 缓存统计可供 CLI **可视化**

用法：`uv run python scripts/verify_research_offline.py`
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.infra.retrieval import (  # noqa: E402
    MockResearchProvider,
    ResearchCache,
    research_topic,
    target_points_for,
)
from storyforge.modes.protocols import ResearchSnippet  # noqa: E402


class FakeTavily:
    """模拟真检索：每轮 4 条、每条约 160 字（够长 → 计为有效）。"""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, q: str, n: int = 5) -> list[ResearchSnippet]:
        self.calls += 1
        return [
            ResearchSnippet(
                title=f"结果{i}",
                url=f"https://example.com/{self.calls}/{i}",
                text=f"关于「{q}」的详细说明。" + "该概念包含多个层次的原理与应用。" * 8,
            )
            for i in range(4)
        ]


def main() -> int:
    print("=" * 76)
    print("① 目标知识点按篇幅档位")
    print("=" * 76)
    for sc in ("fast", "standard", "grand", "ultra"):
        print(f"  {sc:<9} → {target_points_for(sc):>4} 个")

    tmp = Path(tempfile.mkdtemp(prefix="sf_research_")) / "cache.json"
    cache = ResearchCache(path=tmp)

    print()
    print("=" * 76)
    print("② fast 档（目标 8）：达标即停")
    print("=" * 76)
    fake = FakeTavily()
    plan = research_topic("宋代点茶", scale="fast", provider=fake, cache=cache)
    print("\n".join("  " + ln for ln in plan.summary_lines()))
    print(f"\n  provider 实际调用次数：{fake.calls}（达标后不应再搜）")

    print()
    print("=" * 76)
    print("③ 断点续跑：同主题再跑一次")
    print("=" * 76)
    fake2 = FakeTavily()
    plan2 = research_topic("宋代点茶", scale="fast", provider=fake2, cache=cache)
    first = plan2.rounds[0] if plan2.rounds else None
    print(f"  轮次={plan2.rounds_used}  "
          f"首轮={'缓存命中' if first and first.from_cache else '未命中'}")
    print(f"  达标={plan2.reached_target}  原因={plan2.stopped_reason}")
    print(f"  provider 调用={fake2.calls}（缓存命中时不应联网）")

    print()
    print("=" * 76)
    print("④ 缓存统计（CLI 可视化用）")
    print("=" * 76)
    print("  " + json.dumps(cache.stats(), ensure_ascii=False))

    print()
    print("=" * 76)
    print("⑤ Mock provider：结果必须**可识别**（不能被当成真知识来源）")
    print("=" * 76)
    mock = MockResearchProvider()
    sn = mock.search("测试主题")[0]
    checkable = sn.title.startswith("[Mock]") and sn.url == ""
    print(f"  title 带 [Mock] 前缀：{sn.title.startswith('[Mock]')}")
    print(f"  url 为空（不可溯源）：{sn.url == ''}")
    print(f"  → {'✓ 可识别' if checkable else '★ 不可识别（会被误当真结果）'}")

    print()
    print("=" * 76)
    # 判据（不是"轮次尽量少"，而是"**恰好搜到达标**"）：
    #   每轮 4 条 × 0.5 = 2 知识点 → 目标 8 需要 **4 轮**
    #   少搜（<4）说明没达标却提前停；多搜（>4）说明达标后还在烧配额
    expected_rounds = 4
    checks = [
        ("fast 档达标", plan.reached_target),
        ("轮次恰好达标即停", fake.calls == expected_rounds),
        ("缓存命中后不再联网", fake2.calls == 0),
        ("Mock 结果可识别", checkable),
    ]
    for label, ok_i in checks:
        print(f"  {'✓' if ok_i else '★'} {label}")
    ok = all(c for _, c in checks)
    print()
    print(f"  结论：{'✓ 通过' if ok else '★ 不通过'}")
    print("=" * 76)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
