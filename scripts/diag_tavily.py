"""诊断：TAVILY_API_KEY 是否已配置、research 真联网是否可跑。

## 用户决策的背景
当前研究走的是 Mock 模式（结果带 `[Mock]` 前缀 + 空 URL），
只有配上真 Tavily key 才能真正验证 2-2 的联网搜索能力。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from storyforge.config import load_dotenv, get_settings

    load_dotenv()
    s = get_settings()
    tavily_key = os.environ.get(s.research.api_key_env, "")

    print("=" * 72)
    print("TAVILY 配置诊断")
    print("=" * 72)
    print(f"  配置的 provider      : {s.research.api_key_env}")
    print(f"  环境变量存在吗       : {'✓ 有' if tavily_key else '✗ 无'}")
    if tavily_key:
        print(f"  长度（不显示值）      : {len(tavily_key)}")
        print(f"  位置                  : {ROOT / '.env'}")
    else:
        print()
        print("  ★ 需要在 `.env` 里加：`TAVILY_API_KEY=<真实 key>`")
        print("  `.env.example` 里应该有占位模板的，看一下：")
        ex = ROOT / ".env.example"
        if ex.exists():
            t = ex.read_text(encoding="utf-8")
            if "TAVILY_API_KEY" in t:
                print("    ✓ `.env.example` 有 `TAVILY_API_KEY` 模板")
            else:
                print("    ★ `.env.example` **没有** `TAVILY_API_KEY` 模板 —— 需要补")
    print("=" * 72)

    # 真实跑一次（只有配置好 key 时才做）
    if tavily_key:
        print()
        print("=== 真实联网测试（1 次，费用 ≈ 1 个 token）===")
        from storyforge.infra.retrieval.provider import TavilyResearchProvider
        prov = TavilyResearchProvider(api_key=tavily_key)
        result = prov.search("恐龙为什么会灭绝", max_results=3)
        for i, r in enumerate(result, 1):
            src_seen = "[Mock]" if r.title.startswith("[Mock]") else "✓ 真实"
            url = r.url or "(无 URL)"
            print(f"  {i}. {src_seen} {r.title[:50]}")
            print(f"      {url}")
            print(f"      片段：{r.text[:80]}…")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
