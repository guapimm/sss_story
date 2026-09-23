"""用**真实章节正文**复现 scene_extractor 的间歇性截断（确定性优先于猜测）。

## 已知事实

  · 正文真实规模：7018 字符（`data/artifacts/books/19/prose/00001.txt`）
  · 我先前用**短文本**探针 6/6 全过 → 说明短输入复现不了
  · 真实失败发生在 **2887 字符处 EOF**，`finish_reason` 未知（llm_calls 表空）

## 本脚本要回答

用真实 7018 字符正文，重复 N 次：
  1. 会不会复现截断？
  2. 若复现，`finish_reason` 是 `length`（预算不够）还是 `stop`（模型自身问题）？

这个区分决定修法：
  · `length` → 加大 `max_tokens`（或减输出条目数）
  · `stop`   → 归一/修复 JSON（模型自己产出不全）

用法：
    uv run python scripts/repro_scene_extract.py [N]
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROSE = ROOT / "data" / "artifacts" / "books" / "19" / "prose" / "00001.txt"

BASE = "https://token-plan-cn.xiaomimimo.com/v1"
MODEL = "mimo-v2.6-flash"

#: 与 extractors.py 的 SCENE_EXTRACTOR_SYSTEM 同形的任务描述
TASK = """从下面这段小说正文中抽取场景信息。

正文：
{prose}

要求输出 JSON，格式：
{{"summary": "本章场景一句话概述",
 "events": [{{"story_time": "故事内时间", "location": "地点",
             "participants": ["参与角色"], "description": "发生了什么",
             "consequence": "导致的后果"}}]}}

只输出 JSON，不要解释。"""


def _key() -> str:
    if not os.environ.get("MIMO_API_KEY"):
        from storyforge.config import load_dotenv
        load_dotenv()
    return os.environ.get("MIMO_API_KEY", "")


def call(prompt: str, max_tokens: int, timeout: int = 600) -> tuple[int, object]:
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "low",
        }).encode(),
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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4

    if not PROSE.exists():
        print(f"★ 找不到正文：{PROSE}")
        print("  先跑一次 `uv run storyforge write --book-id 19 --chapters 1`")
        return 1

    prose = PROSE.read_text(encoding="utf-8")
    prompt = TASK.format(prose=prose)

    print("=" * 78)
    print("scene_extractor 真实规模复现")
    print("=" * 78)
    print(f"  正文：{len(prose)} 字符")
    print(f"  prompt：{len(prompt)} 字符")
    print(f"  重复：{n} 次")
    print()

    bad = 0
    for i in range(1, n + 1):
        t0 = time.time()
        st, resp = call(prompt, 32768)
        dt = time.time() - t0
        if st != 200 or not isinstance(resp, dict):
            bad += 1
            print(f"  [{i}] HTTP {st}  ★ {str(resp)[:100]}")
            continue
        ch = (resp.get("choices") or [{}])[0]
        fr = ch.get("finish_reason")
        u = resp.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        rt = det.get("reasoning_tokens") or 0
        out = u.get("completion_tokens") or 0
        c = (ch.get("message") or {}).get("content") or ""
        ok = "OK"
        try:
            obj = json.loads(c)
            n_ev = len(obj.get("events") or [])
            ok = f"OK events={n_ev}"
        except ValueError as e:
            bad += 1
            ok = f"★ {str(e)[:52]}"
        print(f"  [{i}] finish={str(fr):<8} out={out:<6} reasoning={rt:<6} "
              f"len={len(c):<6} {dt:5.1f}s  {ok}")

    print()
    print("=" * 78)
    print(f"失败 {bad}/{n}")
    if bad:
        print("  ★ 若 finish=length → 加大预算；若 finish=stop → 需 JSON 归一")
    else:
        print("  ✓ 本规模下稳定 —— 说明失败与输入规模/预算的组合相关，非必然")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
