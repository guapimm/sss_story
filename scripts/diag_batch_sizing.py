"""量出**一批细纲的真实输出规模与耗时** —— 定批次大小与超时的数据依据。

## 要回答的问题

书 16 的 15 章批次连续 3 次超时（每次 300s，共 1135s）失败。
prompt 只有 3635 字符 —— 说明瓶颈**不是输入**，而是**输出**：
每章要 4~8 个场景拍点，每个拍点 4 个必填字段，15 章 = 大量生成。

本脚本按 3 / 6 / 15 章各跑一次，量：
  · 实际 completion tokens（输出规模）
  · 实际耗时（秒）
  · 产出的场景数、每章 JSON 字符数
→ 据此给出**批次大小**与**超时上限**的建议值（而不是拍脑袋）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from storyforge.config import get_settings, load_dotenv  # noqa: E402

load_dotenv()

from storyforge.graph.nodes.detail_planner import (  # noqa: E402
    build_detail_batch,
    plan_to_dict,
)
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402

BID = 16
#: 只测 1 / 3 / 6 章 —— 15 章已知会超时（300s），再测一遍是白烧 token。
#: 目的是量出**每章成本**，据此反推批次上限。
BATCHES = (1, 3, 6)


async def run_one(store: ArtifactStore, span: int) -> dict:
    from storyforge.graph.nodes.skeleton_planner import load_skeleton
    from storyforge.graph.nodes.volume_planner import load_series_plan

    bible = store.read_text(f"books/{BID}/bible.md")
    sk = load_skeleton(store, BID, 1)
    series = load_series_plan(store, BID)
    vol = next(v for v in series.volumes if v.volume_no == 1)

    # 超时给足够大，本脚本目的是**量出真实耗时**，不是复现超时
    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=1, structured_retries=0, timeout_s=1800))
    t0 = time.monotonic()
    r = await build_detail_batch(
        client, book_id=BID, volume=vol, skeleton=sk, bible_text=bible,
        batch_start=1, batch_end=span, max_rounds=0,
    )
    secs = time.monotonic() - t0

    rec = client.records[0] if client.records else None
    scenes = sum(len(p.scenes) for p in r.plans.values())
    # ⚠️ 序列化函数在 `detail_planner` 模块里（`ChapterPlan` 本身没有 to_dict）
    chars = sum(
        len(json.dumps(plan_to_dict(p), ensure_ascii=False))
        for p in r.plans.values()
    )
    return {
        "chapters": span,
        "ok": r.ok,
        "plans": len(r.plans),
        "seconds": round(secs, 1),
        "secs_per_chapter": round(secs / span, 1),
        "completion_tokens": rec.usage.completion_tokens if rec else None,
        "reasoning_tokens": rec.usage.reasoning_tokens if rec else None,
        "prompt_tokens": rec.usage.prompt_tokens if rec else None,
        "finish_reason": rec.finish_reason if rec else None,
        "scenes_total": scenes,
        "scenes_per_chapter": round(scenes / len(r.plans), 1) if r.plans else None,
        "out_chars": chars,
        "chars_per_chapter": round(chars / len(r.plans)) if r.plans else None,
        "error": (r.error or "")[:150],
    }


async def main() -> int:
    store = ArtifactStore.from_settings(get_settings())
    print("=" * 90)
    print("细纲批次规模实测（book 16，卷 1）")
    print("=" * 90)
    rows = []
    for span in BATCHES:
        print(f"\n--- 批次 {span} 章 ---", flush=True)
        try:
            row = await run_one(store, span)
        except Exception as exc:  # noqa: BLE001
            row = {"chapters": span, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:150]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=1), flush=True)

    print()
    print("=" * 90)
    print("汇总")
    print("=" * 90)
    print(f"{'章数':<6}{'OK':<7}{'秒':<9}{'秒/章':<9}{'completion':<12}"
          f"{'场景/章':<9}{'输出字符/章':<11}")
    for r in rows:
        print(f"{r.get('chapters', '?'):<6}{str(r.get('ok')):<7}"
              f"{str(r.get('seconds', '-')):<9}{str(r.get('secs_per_chapter', '-')):<9}"
              f"{str(r.get('completion_tokens', '-')):<12}"
              f"{str(r.get('scenes_per_chapter', '-')):<9}"
              f"{str(r.get('chars_per_chapter', '-')):<11}")

    print()
    print("=" * 90)
    print("建议（数据说话，不拍脑袋）")
    print("=" * 90)
    good = [r for r in rows if r.get("ok") and r.get("secs_per_chapter")]
    if good:
        worst = max(r["secs_per_chapter"] for r in good)
        print(f"  实测每章耗时上限约 {worst:.1f}s（含推理）")
        for cfg_timeout in (300.0, 600.0, 900.0):
            safe = int(cfg_timeout / worst) if worst else 0
            print(f"  超时 {cfg_timeout:.0f}s → 单批最多约 {safe} 章（留 0 余量）")
        print()
        print("  ⚠️ 另需考虑：超时是**整批作废**（失败批次不落盘），")
        print("     所以批次过大 = 一次超时白烧全部 token。批次小、并发高更稳。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
