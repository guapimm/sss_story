"""取证：scene_extractor 在 v2.6-flash 上为何输出被截断（EOF while parsing）。

## 现象

2026-09-23 真实 `write` 崩在 scene_extractor：

    StructuredOutputError: 节点 scene_extractor 结构化输出校验失败（3 次尝试）
      - ``：Invalid JSON: EOF while parsing a list at line 1 column 2887

注意这是**截断**（EOF），**不是**字段名漂移（style_critic 那类）。
两者根因不同，修法也不同 —— 不能混为一谈。

## 要回答的问题

1. 输出真的被 `max_tokens` 截断了吗？（看 finish_reason 与预算）
2. 还是模型自己**主动停**在了半截？（那与预算无关）
3. 重复出现率多少？（稳定性 vs 偶发）

判据全都取自 API 响应本身（`finish_reason` / `usage`），不靠猜。

用法：
    uv run python scripts/diag_scene_extract_truncation.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://token-plan-cn.xiaomimimo.com/v1"
MODEL = "mimo-v2.6-flash"
REPEAT = 3

# 与 extractors.py 的真实 prompt 同形（场景抽取）
PROMPT = """从下面这段小说正文中抽取场景信息。

正文：
林默在八层走廊醒来，后脑勺着地，只记得自己叫林默。他摸到口袋里有一部死机的手机
和一本黑色封皮的《住户须知》。走廊极长，墙皮呈规则方形剥落。
第七条写着：不要给任何人开门。一个中年男人冲上来敲门，门内伸出东西把他拖了进去。
林默逃进楼梯间，读完了剩下的条目。上方传来脚步声，只有一个人，正在往下走。

要求输出 JSON，格式：
{"summary": "本章场景一句话概述",
 "events": [{"story_time": "故事内时间", "location": "地点",
             "participants": ["参与角色"], "description": "发生了什么",
             "consequence": "导致的后果"}]}

只输出 JSON，不要解释。"""


def _key() -> str:
    if not os.environ.get("MIMO_API_KEY"):
        from storyforge.config import load_dotenv
        load_dotenv()
    return os.environ.get("MIMO_API_KEY", "")


def call(body: dict, timeout: int = 300) -> tuple[int, object]:
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps({"model": MODEL, **body}).encode(),
        headers={"Authorization": "Bearer " + _key(),
                 "Content-Type": "application/json"},
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def main() -> int:
    print("=" * 76)
    print("scene_extractor 截断取证")
    print("=" * 76)

    # 两组预算：一组贴近实际（32768），一组故意小，用于对照
    for budget in (32768, 2000):
        print()
        print(f"--- max_tokens={budget} ---")
        for i in range(1, REPEAT + 1):
            t0 = time.time()
            st, resp = call({
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": budget,
                "response_format": {"type": "json_object"},
                "reasoning_effort": "low",      # scene_extractor 声明 minimize
            })
            dt = time.time() - t0
            if st != 200 or not isinstance(resp, dict):
                print(f"  [{i}] HTTP {st}  ★ {str(resp)[:90]}")
                continue
            ch = (resp.get("choices") or [{}])[0]
            fr = ch.get("finish_reason")
            u = resp.get("usage") or {}
            det = u.get("completion_tokens_details") or {}
            rt = det.get("reasoning_tokens") or 0
            out = u.get("completion_tokens") or 0
            c = (ch.get("message") or {}).get("content") or ""
            parsed = "OK"
            try:
                json.loads(c)
            except ValueError as e:
                parsed = f"★ {str(e)[:48]}"
            print(f"  [{i}] finish={fr:<8} out={out:<6} reasoning={rt:<6} "
                  f"len={len(c):<6} {dt:5.1f}s  json={parsed}")

    print()
    print("=" * 76)
    print("判读")
    print("=" * 76)
    print("  · finish_reason=length       → 被 max_tokens 截断（加大预算或减输出量）")
    print("  · finish_reason=stop 且非法  → 模型**自己**产出不完整 JSON（另一类问题）")
    print("  · 小预算组复现截断           → 佐证截断机制，可与大预算组对照")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
