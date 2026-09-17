"""取证：把 `scene_extractor` 的 **完整** `state_updates` 打出来。

## 为什么需要它（`diag_extract_shape.py` 不够用）

既有的 `scripts/diag_extract_shape.py` 看的是**书 15 第 1 章**，
而本次失败发生在**书 19 第 1 章**（阶段 1 收尾实测）。
形状漂移是**逐轮**的，必须看**实际失败那一章**的形状。

且报错信息只留 `原始输出前 300 字`（`client.py:597`）——
300 字根本到不了 `state_updates`（它在 summary/events 之后）。
本脚本打印**完整**字段内容，并给出"归一后能否通过"的**最终判据**。

用法：
    uv run python scripts/diag_scene_list_shape_real.py            # 书 19 第 1 章
    uv run python scripts/diag_scene_list_shape_real.py 15 1       # 指定书/章

⚠️ 会真调一次 LLM（约 1 次调用）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.config import load_dotenv  # noqa: E402

load_dotenv()

from storyforge.graph.nodes.extractors import SCENE_EXTRACTOR_SYSTEM  # noqa: E402
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.extraction_schemas import SceneExtractionOut  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402


def _find_prose(book_id: int, chapter: int) -> tuple[str, str]:
    store = ArtifactStore.from_settings()
    ref = f"books/{book_id}/prose/{chapter:05d}.txt"
    try:
        return store.read_text(ref), ref
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"读不到正文 {ref}：{exc}") from exc


async def main() -> int:
    book_id = int(sys.argv[1]) if len(sys.argv) > 1 else 19
    chapter = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    prose, ref = _find_prose(book_id, chapter)
    print(f"正文：{ref}（{len(prose)} 字符）")

    client = LLMClient(
        make_transport(),
        config=ClientConfig(max_attempts=1, structured_retries=0, timeout_s=600),
    )
    res = await client.complete(
        node="scene_extractor", system=SCENE_EXTRACTOR_SYSTEM,
        user=prose, json_mode=True,
    )
    raw = res.text or ""
    print(f"输出 {len(raw)} 字符")

    try:
        d = json.loads(LLMClient._extract_json(raw))
    except Exception as exc:  # noqa: BLE001
        print(f"★ JSON 解析失败：{exc}\n{raw[:1500]}")
        return 1

    print()
    print("=" * 78)
    print("顶层字段")
    print("=" * 78)
    for k, v in d.items():
        n = len(v) if isinstance(v, (list, dict, str)) else ""
        print(f"  {k:<16} {type(v).__name__:<6} {n}")

    print()
    print("=" * 78)
    print("★★★ state_updates 的**完整**内容（报错点）")
    print("=" * 78)
    su = d.get("state_updates")
    print(f"外层类型：{type(su).__name__}")
    if isinstance(su, list):
        for i, item in enumerate(su):
            print(f"\n  [{i}] type={type(item).__name__}")
            print(f"      {json.dumps(item, ensure_ascii=False)}")
    else:
        print(json.dumps(su, ensure_ascii=False, indent=2)[:2000])

    # ---- 最终判据：整份输出能否通过 schema ----
    print()
    print("=" * 78)
    print("★ 最终判据：`SceneExtractionOut` 能否通过")
    print("=" * 78)
    try:
        out = SceneExtractionOut.model_validate(d)
        print("  ✅ 通过")
        for u in out.state_updates:
            print(f"     entity={u.entity!r} field={u.field!r} value={u.value[:50]!r}")
    except Exception as exc:  # noqa: BLE001
        print("  ❌ 失败：")
        for line in str(exc).splitlines()[:12]:
            print(f"     {line}")

    # 存原始输出，供离线复跑（避免重复烧 token）
    dump = ROOT / "doc" / "research" / f"_diag_scene_raw_b{book_id}c{chapter}.json"
    dump.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始输出已存：{dump.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
