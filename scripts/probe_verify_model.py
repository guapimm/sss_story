"""验证方案 B1 可行性：`mimo-v2.6-pro` 能否胜任「知识点是否被资料支撑」的核验。

## 为什么必须先验证再写进验收标准

`providers.toml` 对 `mimo-v2.6-pro` 有**实测告警**：
    800 token 预算下 **content 为空**（token 全烧在 reasoning）

若核验节点也这样，方案 B1 就是纸面方案 —— 会得到"全是空输出"的假失败。
本项目吃过「写下没验证的方案」的亏（如 verify_learn_real_pipeline.py
从未跑通），故**先实测再定标准**。

## 判据

  1. 能否稳定返回**可解析的 JSON**（三态判定）
  2. `max_tokens` 需要多大才不空输出
  3. 判定是否**合理**（给一条明显有据、一条明显无据的，看它分不分得清）

用法：
    uv run python scripts/probe_verify_model.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://token-plan-cn.xiaomimimo.com/v1"
GENERATOR = "mimo-v2.6-flash"
VERIFIER = "mimo-v2.6-pro"

#: 核验 prompt（**独立于生成 prompt**，这是防自证的第一层）
VERIFY_PROMPT = """你是事实核查员。判断下面每条知识点**是否被给出的研究资料支撑**。

## 判定标准（三态，只能选一个）

- `supported`：资料里有**直接依据**（能找到对应句子）
- `inferred` ：资料未直说，但可由资料**合理推得**（教学需要讲透，合理推演允许）
- `unsupported`：资料里**找不到任何依据**（疑似编造）

⚠️ `inferred` 不是 `unsupported` —— 不要把"资料没逐字写"当成编造。

## 研究资料

{evidence}

## 待核验的知识点

{points}

## 输出格式（字段名照抄）

```json
{{
  "verdicts": [
    {{"point": "知识点名", "verdict": "supported",
      "reason": "资料第 3 条明确写了……"}}
  ]
}}
```

只输出 JSON，不要解释。"""

#: 资料：只讲「点茶」本身，**不提**「斗茶」与「水丹青」
EVIDENCE = """[1] 点茶是宋代流行的饮茶方式，将茶粉置于盏中，注入少量沸水调成膏状。
[2] 随后分次注水，用茶筅快速击拂，使茶汤表面形成细腻的白色沫饽。
[3] 核心器具包括茶盏、茶筅、茶匙与汤瓶。
[4] 点茶讲究「七汤」之法，即分七次注水击拂，每次手法不同。"""

#: 三点：前两条有据，第三条**资料完全没提**（故意的）
POINTS = """1. 点茶定义 —— 将茶粉调膏后分次注水击拂的饮茶方式
2. 核心茶具 —— 茶盏、茶筅、茶匙与汤瓶
3. 斗茶文化与水丹青艺术 —— 宋代文人以茶汤作画、比拼茶艺的活动"""


def _key() -> str:
    if not os.environ.get("MIMO_API_KEY"):
        from storyforge.config import load_dotenv
        load_dotenv()
    return os.environ.get("MIMO_API_KEY", "")


def call(model: str, prompt: str, max_tokens: int, timeout: int = 600) -> tuple[int, object]:
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps({
            "model": model,
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
    prompt = VERIFY_PROMPT.format(evidence=EVIDENCE, points=POINTS)

    print("=" * 78)
    print(f"方案 B1 可行性验证：{VERIFIER} 做知识点核验")
    print("=" * 78)
    print(f"  prompt：{len(prompt)} 字符")
    print(f"  预期：第 1、2 条 supported/inferred，第 3 条 unsupported")
    print()

    # 扫描 max_tokens —— 已知该模型在 800 时 content 为空
    for mt in (800, 4000, 12000):
        st, resp = call(VERIFIER, prompt, mt)
        if st != 200 or not isinstance(resp, dict):
            print(f"  max_tokens={mt:<6} HTTP {st}  ★ {str(resp)[:90]}")
            continue
        ch = (resp.get("choices") or [{}])[0]
        fr = ch.get("finish_reason")
        u = resp.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        rt = det.get("reasoning_tokens") or 0
        out = u.get("completion_tokens") or 0
        c = (ch.get("message") or {}).get("content") or ""
        note = "OK"
        if not c.strip():
            note = "★ 空输出"
        else:
            try:
                obj = json.loads(c)
                vs = obj.get("verdicts") or []
                got = [v.get("verdict") for v in vs if isinstance(v, dict)]
                note = f"verdicts={got}"
            except ValueError as e:
                note = f"★ 非法 JSON: {str(e)[:40]}"
        print(f"  max_tokens={mt:<6} finish={str(fr):<7} out={out:<6} "
              f"reasoning={rt:<6} {note}")

    print()
    print("=" * 78)
    print("判读")
    print("=" * 78)
    print("  · 若 800 空输出而 4000+ 正常 → 核验节点预算须 ≥4000（写进配置）")
    print("  · 若第 3 条被正确判 unsupported → 方案 B1 有效")
    print("  · 若全判 supported → 核验模型无区分力，B1 不可用，需换 B3（异厂商）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
