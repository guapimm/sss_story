"""用**裸 httpx** 直连 provider —— 隔离"服务端故障"与"我们的请求有问题"。

## 为什么要这一步

健康检查显示 6/6 全 500。但**不能就此断定是服务端的问题** ——
必须排除"我们的 transport 发了畸形请求导致 500"。

旁证可疑点：langchain-openai 打过警告

    WARNING! response_format is not default parameter.
    response_format was transferred to model_kwargs.

若我们的请求体里有服务端不认识/格式错的字段，也可能被 500 掉。
因此这里**完全绕开 langchain**，用 httpx 发一个最小合法请求：
  · 若裸请求成功 → 问题在我们的 transport
  · 若裸请求也 500 → 确认是服务端故障
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from storyforge.config import get_settings, load_dotenv  # noqa: E402

load_dotenv()

import httpx  # noqa: E402


def _find_provider() -> tuple[str, str, str]:
    """从 providers.toml 取 base_url / api_key_env / 一个可用模型 id。"""
    from storyforge.infra.llm.capability import load_registry  # noqa: PLC0415

    reg = load_registry()
    for name, p in reg.providers.items():
        key = os.environ.get(p.api_key_env or "", "")
        if key:
            # 取该 provider 下第一个模型
            for m in reg.models:
                if m.provider == name:
                    return p.base_url.rstrip("/"), key, m.id
    raise SystemExit("没有找到任何已配置密钥的 provider（.env 里没有 key）")


async def main() -> int:
    base_url, key, model = _find_provider()
    url = f"{base_url}/chat/completions"
    print(f"  目标: {url}")
    print(f"  模型: {model}")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "回复：OK"}],
        "max_tokens": 16,
    }

    async with httpx.AsyncClient(timeout=60.0) as c:
        print()
        print("=" * 70)
        print("① 最小请求（只有 model / messages / max_tokens）")
        print("=" * 70)
        r = await c.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        print(f"  HTTP {r.status_code}")
        print(f"  body: {r.text[:400]}")

        print()
        print("=" * 70)
        print("② 加 json_object（我们 transport 会发的形态）")
        print("=" * 70)
        payload2 = dict(payload, response_format={"type": "json_object"})
        r2 = await c.post(
            url, json=payload2,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        print(f"  HTTP {r2.status_code}")
        print(f"  body: {r2.text[:400]}")

        print()
        print("=" * 70)
        print("③ GET /models（看服务本身是否可应答）")
        print("=" * 70)
        try:
            r3 = await c.get(f"{base_url}/models",
                             headers={"Authorization": f"Bearer {key}"})
            print(f"  HTTP {r3.status_code}")
            print(f"  body: {r3.text[:300]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  异常: {type(exc).__name__}: {exc}")

    print()
    print("=" * 70)
    print("结论")
    print("=" * 70)
    ok_min = r.status_code == 200
    ok_json = r2.status_code == 200
    if ok_min:
        print("  ✅ 裸最小请求**成功** → 服务本身可用。")
        print("     若①成功而②失败，问题在 `response_format`（我们的 transport）。")
        print(f"     （实测：①={r.status_code} ②={r2.status_code}）")
    else:
        print(f"  ❌ 裸最小请求也失败（HTTP {r.status_code}）→ **确认是服务端故障**，")
        print("     与我们的 transport / 代码无关。")
        print("     此时不应据此判断链路缺陷，应等待服务恢复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
