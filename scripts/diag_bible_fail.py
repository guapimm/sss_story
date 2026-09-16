"""查 `bible:parse` 的真实原因 —— 分类标签不够，必须看原始 violations。

本轮 e2e 报 `bible:parse` 且 `bible_calls=0`：
`bible_calls=0` 说明**从没成功调用过一次**（幂等命中也不计），
所以要么 provider 调用失败，要么结构化解析失败。
两者的修法完全不同，必须看原始消息。
"""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "src")

from storyforge.config import load_dotenv  # noqa: E402

load_dotenv()

from storyforge.graph.nodes.bible_architect import build_bible  # noqa: E402
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402

TOPIC = "现代悬疑：一桩十二年前的旧案重开，唯一的目击者如今是负责此案的警官"


async def main() -> int:
    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=2, structured_retries=1, timeout_s=600))
    r = await build_bible(
        client, book_id=16, topic=TOPIC, genre="mystery", max_rounds=2)

    print("=" * 78)
    print("build_bible 结果")
    print("=" * 78)
    print(f"  ok        = {r.ok}")
    print(f"  llm_calls = {r.llm_calls}")
    print(f"  error     = {r.error!r}")
    print(f"  violations:")
    for v in r.violations:
        print(f"    - {v}")
    print()
    print("=" * 78)
    print("调用记录（诊断各次尝试的真实状态）")
    print("=" * 78)
    for i, rec in enumerate(client.records, 1):
        print(f"  [{i}] status={rec.status} finish={rec.finish_reason} "
              f"err={rec.error_type}")
        print(f"      prompt={getattr(rec, 'prompt_total_chars', '?')} "
              f"response={getattr(rec, 'response_chars', '?')} "
              f"reasoning={rec.usage.reasoning_tokens} "
              f"completion={rec.usage.completion_tokens}")
    print()
    print(json.dumps({"violations": r.violations}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
