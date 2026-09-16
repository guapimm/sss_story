"""取证：圣经 `rules` 里到底有没有混进**其他字段的内容**（错位真伪判定）。

## 为什么需要它（D-16 取证流程的一环）

`bible:misnest` 曾在多书测试里让整本书的圣经生成失败。但"报错"不等于"模型错了"：

  · **真错位** —— 模型确实把 `era`/`tone`/`terms` 写进了 `rules`（该改 schema/prompt）
  · **误判** —— 模型没错，是我们认错了（该改校验器）

处置相反，必须**先看原始输出再动手**。本脚本直接打 provider，
把原始 JSON 的 keys / `rules` 里疑似错位的条目 / 校验器判定全部落出来。

## 实测结论（2026-09，小米 MiMo）

**真错位，且是间歇性的**：

    "rules": [ ..., "era：永恒黄昏时代。档案馆内无自然光照变化…",
                     "tone：冷峻、压抑、充满未知威胁的悬疑基调…" ]

后果是 `era` / `tone` / `terms` **三个顶层字段全为空**。
同一本书连续 3 次调用全部正常，多书测试那一轮恰好撞上 → `bible:misnest` 卡死链路。

**处置**（已落地）：`BibleOut` 改为**自动归位**（`_relocate_misnested_fields`），
而不是"拒绝 + 重试" —— 「间歇性结构错位」不该靠重试硬扛，
信息没丢就归位（与 `terms` / `rules` 形状漂移同一原则）。

用法：
    uv run python scripts/diag_bible_misnest.py 16 mystery
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.path.insert(0, "src")

from storyforge.config import load_dotenv  # noqa: E402

load_dotenv()

from storyforge.graph.nodes.bible_architect import (  # noqa: E402
    BIBLE_ARCHITECT_SYSTEM,
    build_bible,
    validate_bible,
)
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("book_id", type=int, help="只用于日志标记，不影响输出")
    ap.add_argument("genre", nargs="?", default="mystery")
    ap.add_argument("--samples", type=int, default=2, help="裸调用采样次数")
    args = ap.parse_args()

    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=1, timeout_s=600, structured_retries=0))
    user = f"【主题】见脚本主题常量\n【题材】{args.genre}\n\n请构建世界观圣经。"

    out: dict = {}
    for i in range(args.samples):
        result = await client.complete(
            node="bible_architect", system=BIBLE_ARCHITECT_SYSTEM,
            user=user, json_mode=True,
        )
        raw = result.text or ""
        rec: dict = {
            "round": i + 1,
            "chars": len(raw),
            "finish_reason": result.record.finish_reason,
            "usage": {
                "prompt": result.record.usage.prompt_tokens,
                "completion": result.record.usage.completion_tokens,
                "reasoning": result.record.usage.reasoning_tokens,
            },
        }
        try:
            d = json.loads(LLMClient._extract_json(raw))
            rec["keys"] = sorted(d)
            rec["era"] = (d.get("era") or "")[:60]
            rec["terms_count"] = len(d.get("terms") or [])
            rules = d.get("rules") or []
            rec["rules_count"] = len(rules)
            known = {"title", "worldview", "power_system", "rules",
                     "era", "tone", "terms"}
            rec["misplaced_in_rules"] = [
                str(r)[:70] for r in rules
                if (head := str(r).split("：", 1)[0].split(":", 1)[0].strip().lower())
                in known
            ]
            from storyforge.infra.llm.schemas import BibleOut  # noqa: PLC0415

            try:
                # ★ 归位后再验：能过说明"错位"已被自动修复（这正是预期行为）
                b = BibleOut.model_validate(d)
                rec["validator"] = validate_bible(b) or "通过"
                rec["after_relocate"] = {
                    "era": (b.era or "")[:60],
                    "tone": (b.tone or "")[:60],
                    "terms_count": len(b.terms),
                    "rules_count": len(b.rules),
                }
            except Exception as exc:  # noqa: BLE001
                rec["validator"] = f"Pydantic 拒绝: {str(exc)[:120]}"
        except Exception as exc:  # noqa: BLE001
            rec["parse_error"] = str(exc)[:120]
            rec["raw_tail"] = raw[-200:]

        out[f"sample_{i + 1}"] = rec

    r = await build_bible(client, book_id=args.book_id, topic="（见脚本）",
                          genre=args.genre, max_rounds=3)
    out["build_bible"] = {
        "ok": r.ok, "calls": r.llm_calls,
        "violations": r.violations, "error": r.error,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
