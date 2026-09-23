"""补充探测：v2.6-flash 在**真实抽取形状**下是否稳定（空输出 / 形状漂移）。

## 为什么要单独探这一项

上一步 `probe_mimo_v26.py` 已确认：v2.6 **无法真正关闭思考**
（`enable_thinking=False` 后 reasoning 仍占 88~97%），
且 `mimo-v2.6-pro` 在 800 token 预算下 **content 直接为空**。

抽取类节点（scene_extractor / entity_resolver / relation_extractor）
声明的是 `thinking = "minimize"`，而本项目**吃过这个亏**：
旧项目 `entity_classify` 空输出 **100%**，根因就是 reasoning 吃光 completion。

因此必须验证：在**抽取节点真实的预算与 prompt**下，v2.6-flash 会不会
重复那个失败模式。判据是**多次重复**——单次成功不能说明稳定。

用法：
    uv run python scripts/probe_mimo_v26_extract.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://token-plan-cn.xiaomimimo.com/v1"
MODEL = "mimo-v2.6-flash"
REPEAT = 5

# 抽取节点的真实任务形状：给内容 + 要求结构化输出
EXTRACT_PROMPT = """从下面这段小说正文中抽取场景状态变化。

正文：
林默推开档案室的门，霉味扑面而来。他在第三排架子前停下，
抽出一份标注着"1998-07"的卷宗。翻到第二十三页时，他的手停住了——
那页纸被人撕掉了一半，剩下的部分只有一行字："……转移到长明楼。"

要求输出 JSON，格式：
{"state_updates": [{"entity": "实体名", "field": "字段", "value": "值"}],
 "scene_location": "本章发生的地点"}

只输出 JSON，不要解释。"""


def _key() -> str:
    if not os.environ.get("MIMO_API_KEY"):
        from storyforge.config import load_dotenv
        load_dotenv()
    k = os.environ.get("MIMO_API_KEY", "")
    if not k:
        raise SystemExit("未配置 MIMO_API_KEY")
    return k


def call(body: dict, *, timeout: int = 180) -> tuple[int, object]:
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
    print("=" * 74)
    print(f"v2.6-flash 抽取稳定性探测（重复 {REPEAT} 次）")
    print("=" * 74)

    # 抽取节点的真实预算：本项目抽取 prompt 输出上限设得不高，
    # 这里给一个**偏紧但合理**的值，专门试探"思考吃光预算"的边界。
    for max_tokens in (1500, 4000):
        print()
        print(f"--- max_tokens={max_tokens} ---")
        empty = 0
        bad_json = 0
        degraded = 0          # 形状漂移（顶层是 list / 缺关键字段）
        reasons: list[int] = []
        for i in range(1, REPEAT + 1):
            t0 = time.time()
            st, resp = call({
                "messages": [{"role": "user", "content": EXTRACT_PROMPT}],
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                # 抽取节点声明 minimize → 等价于发最低档
                "reasoning_effort": "low",
            })
            dt = time.time() - t0
            if st != 200 or not isinstance(resp, dict):
                print(f"  [{i}] HTTP {st}  ★ {str(resp)[:100]}")
                empty += 1
                continue
            u = resp.get("usage") or {}
            det = u.get("completion_tokens_details") or {}
            rt = det.get("reasoning_tokens") or u.get("reasoning_tokens") or 0
            out = u.get("completion_tokens") or 0
            reasons.append(rt)
            c = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if not c.strip():
                empty += 1
                print(f"  [{i}] ★ 空输出   out={out} reasoning={rt} {dt:.1f}s")
                continue
            try:
                obj = json.loads(c)
            except ValueError:
                bad_json += 1
                print(f"  [{i}] ★ 非法 JSON  out={out} reasoning={rt} {dt:.1f}s"
                      f"  head={c[:60]!r}")
                continue
            if not isinstance(obj, dict) or "state_updates" not in obj:
                degraded += 1
                print(f"  [{i}] ★ 形状漂移  顶层={type(obj).__name__}"
                      f" keys={list(obj)[:4] if isinstance(obj, dict) else '—'}")
                continue
            n = len(obj.get("state_updates") or [])
            print(f"  [{i}] OK  updates={n} out={out} reasoning={rt} {dt:.1f}s")

        avg_r = sum(reasons) / len(reasons) if reasons else 0
        print(f"  小结：空输出={empty} 非法JSON={bad_json} 形状漂移={degraded}"
              f" | 平均 reasoning={avg_r:.0f} tokens")

    print()
    print("=" * 74)
    print("判据：")
    print("  · 空输出 > 0        → 抽取节点不能声明 thinking=forbidden，预算须给足")
    print("  · 形状漂移 > 0      → 需依赖归一兜底（缺陷 11 的三层修法）")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
