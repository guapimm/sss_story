"""复现 `scene_extractor` 的失败 —— 看模型到底给了什么形状。

实测报错：`缺必填字段：state_updates.0.entity / .field / .value`，
而原始输出里能看到 `events[0]` 用了 `"time"` 与中文键 `"后果"`。

必须看清 `state_updates` 的真实形状，才能决定是"归一"还是"该报错"。
"""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "src")

from storyforge.config import load_dotenv  # noqa: E402

load_dotenv()

from storyforge.graph.nodes.extractors import SCENE_EXTRACTOR_SYSTEM  # noqa: E402
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402

#: 用真实正文（书 15 第 1 章）
import pathlib  # noqa: E402

PROSE = ""
for p in sorted(pathlib.Path("data/artifacts/books/15/prose").glob("*.txt")):
    PROSE = p.read_text(encoding="utf-8")
    print(f"用正文：{p.name}（{len(PROSE)} 字符）")
    break
if not PROSE:
    raise SystemExit("没有已落盘的正文可测（先跑 storyforge write）")


async def main() -> int:
    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=1, structured_retries=0, timeout_s=600))
    res = await client.complete(
        node="scene_extractor", system=SCENE_EXTRACTOR_SYSTEM,
        user=PROSE, json_mode=True,
    )
    raw = res.text or ""
    print(f"\n输出 {len(raw)} 字符")
    try:
        d = json.loads(LLMClient._extract_json(raw))
    except Exception as exc:  # noqa: BLE001
        print(f"解析失败：{exc}")
        print(raw[:1500])
        return 1

    print()
    print("=" * 80)
    print("顶层 keys")
    print("=" * 80)
    for k in d:
        v = d[k]
        n = len(v) if isinstance(v, (list, dict, str)) else "?"
        print(f"  {k:<16} {type(v).__name__:<8} len={n}")

    print()
    print("=" * 80)
    print("★ state_updates 的真实形状（报错点）")
    print("=" * 80)
    su = d.get("state_updates")
    if not su:
        print("  （没有 state_updates）")
    elif isinstance(su, dict):
        print("  ★ 这一轮是 **dict**（不是 list）—— 又一种形状：")
        for k, v in list(su.items())[:4]:
            print(f"      {k!r}: {json.dumps(v, ensure_ascii=False)[:200]}")
    else:
        for i, item in enumerate(su[:4]):
            print(f"  [{i}] type={type(item).__name__}")
            print(f"      {json.dumps(item, ensure_ascii=False)[:400]}")
            # ★ 立刻试一下归一后能否通过 —— 这是最终判据
            from storyforge.infra.llm.schemas import StateUpdateOut  # noqa: PLC0415

            try:
                s = StateUpdateOut.model_validate(item)
                print(f"      → 归一成功：entity={s.entity!r} "
                      f"field={s.field!r} value={s.value[:40]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"      → ★ 归一**仍失败**：{str(exc)[:200]}")

    print()
    print("=" * 80)
    print("events[0] 的真实形状（字段名漂移）")
    print("=" * 80)
    ev = (d.get("events") or [None])[0]
    if ev:
        print(f"  keys = {sorted(ev)}")
        print(f"  {json.dumps(ev, ensure_ascii=False)[:400]}")

    print()
    print("=" * 80)
    print("injury_updates 的形状（对照）")
    print("=" * 80)
    iu = d.get("injury_updates")
    if isinstance(iu, dict):
        print(f"  ★ 也是 dict：{list(iu)[:4]}")
    else:
        for i, item in enumerate((iu or [])[:2]):
            print(f"  [{i}] keys="
                  f"{sorted(item) if isinstance(item, dict) else type(item).__name__}")
    print(json.dumps({"raw_keys": sorted(d)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
