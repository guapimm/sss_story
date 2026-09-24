"""验证「核验器本身」有没有区分力 —— 给它一条**明显编造**的知识点，看它抓不抓。

## 为什么必须做这一步

真实链路（书 993）跑出 **准确率 100%（0 条无据）**。这有两种可能：

  · 好事：地图确实都有依据
  · 坏事：**核验器是个恒真断言** —— 永远说"通过"

本项目已反复吃过"抓不到问题的检查等于没有"的亏
（规则 3 四类漏检、规则 22 完全失效、`validate_map` 只查"有没有"）,
故对**核验器自身**也必须证伪一次。

## 判据

给同一份真实资料 + 一组**混合**知识点（真的 + 编的），
核验器必须把**编的那条**判成 `unsupported`。

若全判 supported → B1 核验无效，必须上报而不是当成"地图很好"。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from storyforge.config import load_dotenv
    from storyforge.graph.nodes.learning import verify_learning_map
    from storyforge.infra.llm.client import ClientConfig, LLMClient
    from storyforge.infra.llm.transport import make_transport

    load_dotenv()

    # ① 取**真实**研究资料（与书 993 同源）
    cache = json.loads(
        (ROOT / "data/research_cache.json").read_text(encoding="utf-8"))
    entry = next(iter(cache.values()))
    snippets = (entry.get("result") or {}).get("snippets") or []
    print(f"资料：{len(snippets)} 条（全部来自真实联网检索）")

    # ② 混合知识点：前两条来自真实地图，第三条**故意编造**
    points = [
        ("点茶的源流与地位", "点茶在宋代流行，后衍生为日本抹茶道。"),
        ("茶筅与击拂", "用茶筅快速击拂使茶汤形成沫饽。"),
        # ★ 编造：资料里绝不会有这些内容（宋代人不可能用现代科技）
        ("量子点茶机的磁悬浮击拂原理",
         "南宋临安茶坊已采用磁悬浮茶筅，配合量子纠缠控温，"
         "可在零点三秒内完成七汤击拂。"),
    ]
    print(f"知识点：{len(points)} 条（第 3 条为**故意编造**）")
    print()

    from storyforge.config import get_settings
    s = get_settings()
    client = LLMClient(
        make_transport(),
        config=ClientConfig(max_attempts=s.llm.max_attempts,
                            timeout_s=s.llm.default_timeout_s),
    )

    vout, calls = asyncio.run(verify_learning_map(
        client, topic="宋代点茶", points=points, snippets=snippets,
        book_id=9999, idempotency_key="probe:verify:discrimination",
    ))
    print(f"核验完成（LLM 调用 {calls} 次）")
    print("=" * 74)
    for v in (vout.verdicts if vout else []):
        mark = {"supported": "✓", "inferred": "~",
                "unsupported": "★"}.get(v.verdict, "?")
        print(f"  {mark} [{v.verdict:12}] {v.point}")
        print(f"      理由：{v.reason[:70]}")
    print("=" * 74)

    verdicts = {v.point: v.verdict for v in (vout.verdicts if vout else [])}
    # 找编造那条的判定（模型可能改写名字，故按关键字匹配）
    fake = [vd for pt, vd in verdicts.items() if "量子" in pt or "磁悬浮" in pt]
    fake_verdict = fake[0] if fake else None

    print()
    print("判读")
    print("=" * 74)
    ok = True
    if fake_verdict == "unsupported":
        print("  ✓ 编造的知识点被判 unsupported —— **核验器有区分力**")
    elif fake_verdict is None:
        print("  ★ 模型没判这一条（漏判）—— 不可接受")
        ok = False
    else:
        print(f"  ★ 编造的知识点被判 {fake_verdict!r} —— **核验器无区分力**，")
        print("    B1 不可用，必须上报（不能当成「地图质量很好」）")
        ok = False
    print(f"  统计：{vout.counts() if vout else {}}  准确率 {(vout.accuracy() if vout else 0):.0%}")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
