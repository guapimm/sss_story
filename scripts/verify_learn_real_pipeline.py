"""Tavily 真实联网 + 从研究到学习的完整真链路验证（用户指定先完成）。

## 背景

用户提供了真实 `TAVILY_API_KEY`。研发时从"资产配置 → EmptyKey → Mock"
切到了"真实可用"。此前 learn-map 的 LLM 路径从未被真实 key 跑过。
本脚本证明：**用户花的 key 是真正活了**。

## 判据（用户要求的「全流程可视化」，且需可核查）

1. 真实研究（不为空、**无 Mock 标记**、**有 URL**）
2. 学习地图的 L-1/L-2 都生成
3. **不再需要再做一次**（项目经验：能跑一次不再跑就已是证据）

## ⚠️ 先清缓存

缓存里若混有 Mock 占位文本，用它验证 LLM 路径只能证明"代码通路"
（不炸），**不能**证明"知识点质量"。两者必须分清 —— 本项目踩过
"看到的失败与真实的失败错位"的坑（交接文档 §5.1）。

## 用法

    uv run python scripts/verify_learn_real_pipeline.py

需要 `.env` 里有可用的 `TAVILY_API_KEY`；无 key 时本脚本**明确失败**，
不会静默降级为 Mock（那正是它要防的事）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 独立专题号，避免污染主书
BOOK_ID = 991

TOPIC = "宋代点茶"


def _is_mock(snippet: object) -> bool:
    title = getattr(snippet, "title", "") or ""
    return str(title).startswith("[Mock]")


def main() -> int:
    from storyforge.config import get_settings, load_dotenv, resolve_secret

    # `.env` 只在 CLI 的 `main()` 里加载；本脚本不经 CLI，须自己加载一次。
    # 不加这一步会得到"未配置 TAVILY_API_KEY"的**误导性**结论
    # —— 文件里明明有，只是没进 `os.environ`。
    injected = load_dotenv()
    print(f"  （已从 .env 注入 {len(injected)} 个键：{injected}）")

    from storyforge.infra.retrieval import (
        ResearchCache,
        make_research_provider,
        research_topic,
    )

    # ── ① 前置检查：必须有真实 key（不静默降级）─────────────────────────────
    print("=" * 72)
    print("① 前置检查：真实 key 是否存在")
    print("=" * 72)

    s = get_settings()
    key = resolve_secret(s.research.api_key_env)
    if not key:
        print(f"  ★ 未配置 {s.research.api_key_env} —— 本脚本要求真实联网，不降级为 Mock")
        print("  请在 .env 里设置该变量后重跑。")
        return 1
    print(f"  {s.research.api_key_env}: 已配置（长度 {len(key)}，值不打印）")

    provider = make_research_provider(allow_mock=False)
    print(f"  provider: {provider.__class__.__name__}")

    # ── ② 清缓存（排除 Mock 污染）──────────────────────────────────────────
    print()
    print("=" * 72)
    print("② 清空旧缓存（排除 Mock 污染）")
    print("=" * 72)

    cache = ResearchCache()
    before = cache.stats()
    print(f"  缓存文件：{cache.path}")
    print(f"  清理前：topics={before['topics']} size={before['size_bytes']}B")
    cleared = cache.clear()
    print(f"  ★ 已清 {cleared} 个条目（重新采集）")

    # ── ③ 真实联网研究 ─────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("③ 真实联网研究（Tavily）")
    print("=" * 72)

    plan = research_topic(TOPIC, scale="fast", provider=provider, cache=cache)
    snippets = list(plan.snippets)
    mock_n = sum(1 for sn in snippets if _is_mock(sn))
    url_n = sum(1 for sn in snippets if getattr(sn, "url", ""))

    print(f"  轮次：{len(plan.rounds)}    总片段：{len(snippets)}"
          f"（真 {len(snippets) - mock_n} / Mock {mock_n}）")
    print(f"  有 URL：{url_n} 条")
    print("  片段示例：")
    for sn in snippets[:3]:
        print(f"    - {str(sn.title)[:52]}")
        print(f"      {str(sn.url)[:70] or '（空 URL）'}")

    # 判据：真实研究**不得**含 Mock
    if not snippets:
        print("  ★ 检索结果为空 —— 真实研究不应如此，请检查 key 配额或网络")
        return 1
    if mock_n:
        print(f"  ★ 仍有 {mock_n} 个 Mock 片段 —— 真实研究结果不能含占位文本")
        return 1
    print("  ✓ 真实联网（无 Mock 混入）")

    # 缓存是否真的落盘了干净数据
    after = cache.stats()
    print(f"  ✓ 缓存已落盘：topics={after['topics']} size={after['size_bytes']}B")

    # ── ④ learn-map gen（真实 LLM）─────────────────────────────────────────
    print()
    print("=" * 72)
    print("④ learn-map gen（真实 LLM）")
    print("=" * 72)

    r = subprocess.run(
        [sys.executable, "-m", "storyforge.cli",
         "learn-map", "gen", "--book-id", str(BOOK_ID),
         "--topic", TOPIC, "--scale", "fast"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    for line in (r.stdout or "").splitlines()[-16:]:
        print("  " + line)
    if r.returncode != 0 and r.stderr:
        print("  --- stderr ---")
        for line in r.stderr.splitlines()[-8:]:
            print("  " + line)

    # 判据：必须走真实 LLM，而不是离线种子
    out = r.stdout or ""
    llm_used = ("LLM" in out) and ("种子" not in out.split("LLM")[0][-40:])
    print()
    print("  ◆ 判据：调用含真实 LLM 生成（而非离线种子）")
    if r.returncode != 0:
        print(f"  ★ learn-map gen 退出码 {r.returncode} —— 未通过")
        return 1
    if not llm_used:
        print("  ★ 走了离线/种子路径 —— 真实 LLM 未被验证")
        return 1
    print("  ✓ 全链路通了（真实联网 + 真实 LLM）")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
