"""provider 健康检查 —— 区分"代码缺陷"与"服务端 500/限流"。

实测背景：e2e 各轮分别在不同阶段失败（圣经 / 台阶 / 骨架 / 细纲），
其中一轮圣经阶段报 `Error code: 500 - Internal Server Error`。

必须分清楚：
  · **代码缺陷** → 我们修
  · **服务端 5xx / 限流** → 重试或等待，不是缺陷（但要有清晰报错）

本脚本连打 N 次最小请求，给出成功率与错误分布。
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter

sys.path.insert(0, "src")

from storyforge.config import load_dotenv  # noqa: E402

load_dotenv()

from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402

N = 6

#: ⚠️ 必须用一个**已在 `providers.toml` 里声明**的节点名。
#:
#: 决策 D-09 禁止在代码里直接指定模型，节点必须声明能力需求。
#: 探测健康用抽取类节点（`thinking=minimize`）—— 最省，且不干扰规划类统计。
NODE = "scene_extractor"


async def main() -> int:
    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=1, structured_retries=0, timeout_s=120, backoff_base_s=0.0))
    outcomes: list[tuple[str, float]] = []
    print(f"连打 {N} 次最小请求（max_attempts=1，即不重试，看原始成功率）")
    print("=" * 70)
    for i in range(1, N + 1):
        t0 = time.monotonic()
        try:
            res = await client.complete(
                node=NODE, system="你是助手。", user="回复：OK")
            dt = time.monotonic() - t0
            txt = (res.text or "").strip()[:20]
            print(f"  [{i}] OK   {dt:5.1f}s  {txt!r}")
            outcomes.append(("ok", dt))
        except Exception as exc:  # noqa: BLE001
            dt = time.monotonic() - t0
            kind = type(exc).__name__
            msg = str(exc)[:90]
            print(f"  [{i}] FAIL {dt:5.1f}s  {kind}: {msg}")
            outcomes.append((kind, dt))
        # 温和间隔，避免把"限流"误判成"服务端故障"
        await asyncio.sleep(2.0)

    print()
    print("=" * 70)
    print("汇总")
    print("=" * 70)
    counter = Counter(k for k, _ in outcomes)
    ok = counter.get("ok", 0)
    print(f"  成功率：{ok}/{N} = {ok / N:.0%}")
    for k, c in counter.most_common():
        print(f"    {k:<22} {c}")
    times = [t for k, t in outcomes if k == "ok"]
    if times:
        print(f"  成功耗时：min {min(times):.1f}s / "
              f"中位 {sorted(times)[len(times) // 2]:.1f}s / max {max(times):.1f}s")
    print()
    if ok == N:
        print("  → provider 健康。之前的 500 是**瞬时故障**，不是代码缺陷。")
    elif ok == 0:
        print("  → provider 当前不可用（全失败）。这不是代码问题，")
        print("     应等待恢复；期间不应据此判断链路缺陷。")
    else:
        print("  → provider 不稳定（部分失败）。链路失败可能源于此，")
        print("     判断缺陷前需先排除服务端因素。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
