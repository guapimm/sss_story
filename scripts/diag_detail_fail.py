"""查书 16 细纲失败的**真实原因** —— 报错信息是空的，必须看调用记录。

报错：「节点 detail_planner 调用失败（3 次尝试）：」后面什么都没有。
`complete()` 的空输出分支**已经**会写 last_err，所以这次**不是**那条路径。
必须从 transport 调用记录里找真正发生了什么。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from storyforge.config import get_settings, load_dotenv  # noqa: E402

load_dotenv()

from storyforge.graph.nodes.detail_planner import build_detail_batch  # noqa: E402
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402


async def main() -> int:
    print("=" * 78)
    print("直接调用 detail_planner（不经过图），看真实失败原因")
    print("=" * 78)
    store = ArtifactStore.from_settings(get_settings())
    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=1, structured_retries=0, timeout_s=900))

    bible = store.read_text("books/16/bible.md")
    from storyforge.graph.nodes.skeleton_planner import load_skeleton

    sk = load_skeleton(store, 16, 1)
    from storyforge.graph.nodes.volume_planner import load_series_plan

    series = load_series_plan(store, 16)
    vol = next(v for v in series.volumes if v.volume_no == 1)

    print(f"圣经 {len(bible)} 字符｜骨架 places={getattr(sk, 'places', None)}")
    print(f"本卷 {vol.chapter_count} 章｜请求批次 1~3")

    r = await build_detail_batch(
        client, book_id=16, volume=vol, skeleton=sk,
        bible_text=bible, batch_start=1, batch_end=3, max_rounds=0,
    )
    print()
    print(f"ok={r.ok}  plans={len(r.plans)}  calls={r.llm_calls}")
    print(f"error={r.error!r}")
    print(f"lint={ {k: v for k, v in list(r.lint_issues.items())[:2]} }")
    print()
    print("=" * 78)
    print("调用记录（每条的真实状态）")
    print("=" * 78)
    for rec in client.records:
        print(f"  node={rec.node_name} status={rec.status} "
              f"finish={rec.finish_reason} err={rec.error_type}")
        print(f"     prompt={getattr(rec, 'prompt_total_chars', '?')} "
              f"response={getattr(rec, 'response_chars', '?')} "
              f"reasoning={rec.usage.reasoning_tokens} "
              f"completion={rec.usage.completion_tokens}")
        extra = getattr(rec, "extra", None)
        if extra:
            print(f"     extra={extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
