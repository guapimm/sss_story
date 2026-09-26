"""验证结构化重试的幂等缓存交互 —— **修复前后的对照**。

## 修复的缺陷（S0 侦察发现，本探针独立复现）

`complete_structured` 的"带错误回灌的重试"**曾经完全失效**：
`complete()` 在**schema 校验之前**就把原始文本写进幂等缓存，
而修复重试**重用同一个 `idempotency_key`**，于是：

    第 1 次：模型返回非法 JSON → 写进缓存
    第 2 次（修复重试）→ 命中缓存 → 拿到**同一份非法 JSON** → 再失败
    ⇒ 一次格式瑕疵 = 硬失败，零可用输出

**修复前实测**：

    带 idempotency_key : StructuredOutputError，transport 调用 **1** 次
    不带 key           : SUCCESS，transport 调用 **2** 次

★ 影响面：**所有**传 `idempotency_key` 的结构化节点
（抽取 3 + 审校 2 + 章节 2）—— 而它们**都**传 key。

## 修法

`complete(..., cache_result=False)`（由 `complete_structured` 传），
幂等写入改到 **schema 校验通过之后**。

## 本脚本的判据（★ 注意）

修复**之后**跑，两种情况都应是 `SUCCESS` + 2 次调用。
故判据是「**两种情况行为一致且都成功**」，**不是**"缺陷仍存在"。

⚠️ 初版写成 `return 0 if bug else 1`，修复后立刻输出
"缺陷不成立 ⇒ 需推翻 S1 的结论" —— 那是**误导**（它把"已修好"
读成了"从未存在"）。已改为下面的对照判据。
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel


class _Out(BaseModel):
    value: str


class _ScriptedTransport:
    """按顺序返回预置响应，并记录调用次数。

    ★ 契约照抄 `tests/unit/test_llm_client.py:35-50` 的 `FakeTransport`：
    客户端把 transport 当作**可调用对象**（`__call__`），不是 `.ainvoke`。
    初版我写成 `ainvoke` → 报 `object is not callable`。
    """

    def __init__(self, responses: list[str]) -> None:
        self.script = [(r, {"prompt_tokens": 10, "completion_tokens": 5})
                       for r in responses]
        self.calls: list[dict] = []

    async def __call__(self, *, model, provider, system, user, **params):  # noqa: ANN001, ANN003, ANN201
        self.calls.append({"user": user, "params": params})
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[idx]

    @property
    def n_calls(self) -> int:
        return len(self.calls)


def _client(transport):
    from storyforge.infra.llm.client import ClientConfig, LLMClient

    return LLMClient(
        transport,
        config=ClientConfig(max_attempts=1, structured_retries=2,
                            backoff_base_s=0.0, timeout_s=30.0),
    )


async def _run(*, with_key: bool) -> tuple[str, int, str, str | None]:
    """返回 (结果, transport 调用次数, 备注, 缓存内容)。"""
    from storyforge.infra.llm.client import CallContext

    # 第 1 次：非法 JSON（缺必填字段 value）
    # 第 2 次：合法 JSON
    t = _ScriptedTransport(['{"wrong_field": "x"}', '{"value": "ok"}'])
    c = _client(t)
    ctx = CallContext(node="entity_resolver", book_id=1,
                      idempotency_key="probe:key" if with_key else None)
    try:
        out, _call = await c.complete_structured(
            node="entity_resolver", system="s", user="u", schema=_Out, context=ctx,
        )
        cached = c.idempotency.get(ctx.idempotency_key) if ctx.idempotency_key else None
        return f"SUCCESS value={out.value!r}", t.n_calls, "", cached
    except Exception as exc:  # noqa: BLE001
        cached = c.idempotency.get(ctx.idempotency_key) if ctx.idempotency_key else None
        return f"{type(exc).__name__}", t.n_calls, str(exc)[:90], cached


async def main() -> int:
    print("=" * 76)
    print("验证：结构化修复重试与幂等缓存的交互")
    print("=" * 76)
    print("脚本化 transport：第 1 次返回非法 JSON，第 2 次返回合法 JSON")
    print("判据：两种情况应**行为一致**且都 SUCCESS、都用 2 次调用")
    print()
    print("（修复前：带 key 会硬失败且只用 1 次 —— 见模块 docstring）")
    print()

    res_key, calls_key, note_key, cached_key = await _run(with_key=True)
    res_nokey, calls_nokey, note_nokey, cached_nokey = await _run(with_key=False)

    print(f"  带 idempotency_key : {res_key:<30} transport 调用 {calls_key} 次")
    print(f"       缓存内容：{cached_key!r}")
    if note_key:
        print(f"       {note_key}")
    print(f"  不带 key           : {res_nokey:<30} transport 调用 {calls_nokey} 次")
    print(f"       缓存内容：{cached_nokey!r}")
    if note_nokey:
        print(f"       {note_nokey}")
    print()

    ok_key = "SUCCESS" in res_key and calls_key == 2
    ok_nokey = "SUCCESS" in res_nokey and calls_nokey == 2
    cache_ok = cached_key == '{"value": "ok"}' and cached_nokey is None
    print("=" * 76)
    if ok_key and ok_nokey and cache_ok:
        print("修复生效：两种情况都成功且调用 2 次；带 key 的缓存内容与合法 JSON 一致")
    elif not ok_key and ok_nokey and calls_key == 1:
        print("★ 缺陷存在 —— 带 key 时修复重试**没有真正重试**：")
        print("  `complete()` 在校验之前把非法文本写进了缓存，")
        print("  第 2 次命中同一份非法文本 ⇒ 一次格式瑕疵 = 硬失败。")
        print("  影响面：所有传 key 的结构化节点（抽取 3 + 审校 2 + 章节 2）")
    else:
        print("? 结果不符合任何一种预期，需人工判读")
    print("=" * 76)
    return 0 if (ok_key and ok_nokey and cache_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
