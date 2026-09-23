"""实测 MiMo v2.6-flash 的能力，用于更新 providers.toml 的能力声明。

## 为什么必须实测而不能照搬 v2.5 的声明

`providers.toml` 里 v2.5 的每一条能力都是**实测**得出，且有一条很关键：

    ⚠️ 实测补充（小米 MiMo v2.5）：它**无法真正关闭思考** ——
       `enable_thinking: False` 与 `thinking: False` 都被服务端忽略，
       reasoning 仍占 completion 的 **89~92%**（抽取任务实测）。

新模型换了版本号，这些行为**可能全变**。若照搬：
  · 抽取节点声明 `forbidden` 但模型其实关不掉 → 空输出（旧项目 100% 的坑）
  · 声明支持 `reasoning_effort` 但服务端不认 → 400
  · 声明支持 json_mode 但实际不生效 → 静默降级

因此逐项实测，再据实填表。

用法：
    uv run python scripts/probe_mimo_v26.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://token-plan-cn.xiaomimimo.com/v1"
MODELS = ["mimo-v2.6-flash", "mimo-v2.6-pro"]


def _key() -> str:
    if not os.environ.get("MIMO_API_KEY"):
        # `.env` 只在 CLI 的 `main()` 里加载；本脚本不经 CLI，须自己加载一次。
        # 不加这步会得到"未配置 MIMO_API_KEY"的**误导性**结论
        # —— 文件里明明有，只是没进 `os.environ`。
        from storyforge.config import load_dotenv
        load_dotenv()
    k = os.environ.get("MIMO_API_KEY", "")
    if not k:
        raise SystemExit("未配置 MIMO_API_KEY（先在 .env 里设置）")
    return k


def call(model: str, body: dict, *, timeout: int = 120) -> tuple[int, dict | str]:
    """返回 (状态码, 解析后的 json 或原始文本)。"""
    payload = {"model": model, **body}
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + _key(),
            "Content-Type": "application/json",
        },
    )
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
        dt = time.time() - t0
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")
        return e.code, body_txt[:400]
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def usage_of(resp: object) -> dict:
    if not isinstance(resp, dict):
        return {}
    u = resp.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    return {
        "in": u.get("prompt_tokens"),
        "out": u.get("completion_tokens"),
        "reasoning": det.get("reasoning_tokens") or u.get("reasoning_tokens"),
    }


def content_of(resp: object) -> str:
    if not isinstance(resp, dict):
        return ""
    ch = (resp.get("choices") or [{}])[0]
    return (ch.get("message") or {}).get("content") or ""


def main() -> int:
    print("=" * 74)
    print("MiMo v2.6 能力实测")
    print("=" * 74)

    for model in MODELS:
        print()
        print("#" * 74)
        print(f"# {model}")
        print("#" * 74)

        # ---- A. 基线：能否正常调用 ----
        st, resp = call(model, {
            "messages": [{"role": "user", "content": "用一句话说明什么是结构闸门。"}],
            "max_tokens": 800,
        })
        u = usage_of(resp)
        c = content_of(resp)
        print(f"[A] 基线调用            HTTP {st}  usage={u}  content={len(c)}ch")
        print(f"    {c[:90] if c else '（空）'}")
        if st != 200:
            print(f"    ★ 失败：{str(resp)[:200]}")
            continue

        # ---- B. reasoning_effort 是否被接受 ----
        for eff in ("low", "high"):
            st2, resp2 = call(model, {
                "messages": [{"role": "user", "content": "1+1=?"}],
                "max_tokens": 500,
                "reasoning_effort": eff,
            })
            u2 = usage_of(resp2)
            print(f"[B] reasoning_effort={eff:<5} HTTP {st2}  usage={u2}"
                  + ("" if st2 == 200 else f"  ★ {str(resp2)[:120]}"))

        # ---- C. 能否真正关闭思考（v2.5 的关键缺陷）----
        for key in ("enable_thinking", "thinking"):
            st3, resp3 = call(model, {
                "messages": [{"role": "user", "content": "抽取：这句话里的实体。只回 JSON。"}],
                "max_tokens": 600,
                "response_format": {"type": "json_object"},
                key: False,
            })
            u3 = usage_of(resp3)
            ratio = ""
            if u3.get("out") and u3.get("reasoning") is not None:
                ratio = f"  reasoning占比={u3['reasoning'] / u3['out'] * 100:.0f}%"
            print(f"[C] {key}=False      HTTP {st3}  usage={u3}{ratio}")

        # ---- D. json_object 模式 ----
        st4, resp4 = call(model, {
            "messages": [{"role": "user", "content":
                          '抽取事实，输出 JSON {"facts":[{"text":"..."}]}。'
                          '内容：团队决定开启闸门，因为旧项目 400 章挤在第 1 卷。'}],
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
        })
        c4 = content_of(resp4)
        ok4 = False
        if c4:
            try:
                json.loads(c4)
                ok4 = True
            except ValueError:
                pass
        print(f"[D] json_object         HTTP {st4}  usage={usage_of(resp4)}"
              f"  可解析={ok4}")

        # ---- E. json_schema strict ----
        st5, resp5 = call(model, {
            "messages": [{"role": "user", "content":
                          "抽取事实。内容：团队决定开启闸门，因为旧项目 400 章挤在第 1 卷。"}],
            "max_tokens": 1500,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "facts", "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"facts": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"], "additionalProperties": False}}},
                        "required": ["facts"], "additionalProperties": False,
                    },
                },
            },
        })
        c5 = content_of(resp5)
        ok5 = False
        if c5:
            try:
                json.loads(c5)
                ok5 = True
            except ValueError:
                pass
        print(f"[E] json_schema strict  HTTP {st5}  usage={usage_of(resp5)}"
              f"  可解析={ok5}" + ("" if st5 == 200 else f"  ★ {str(resp5)[:120]}"))

        # ---- F. 长输出（写作场景）----
        st6, resp6 = call(model, {
            "messages": [{"role": "user", "content":
                          "写一段 300 字左右的中文小说开头，主题：雨夜档案室。"}],
            "max_tokens": 4000,
        }, timeout=300)
        c6 = content_of(resp6)
        print(f"[F] 长输出(4000预算)    HTTP {st6}  usage={usage_of(resp6)}  content={len(c6)}ch")

    print()
    print("=" * 74)
    print("完成。据实更新 config/providers.toml 的能力声明。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
