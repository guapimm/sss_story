"""变异测试：证明"结构化修复重试 × 幂等缓存"的防护真的有效。

## 为什么必须做

新增的 3 条测试若只是"看起来在测"，那这个缺陷会以别的形式复发。
本项目已栽过**三次**「抓不到问题的检查等于没有」：
规则 3 四类漏检、**规则 22 完全失效**、`validate_map` 只查"有没有"。

★ 本缺陷的特殊危险：它曾**在测试全绿的情况下存在** ——
同场景的旧测试 `test_structured_output_retries_with_error_feedback` 通过了，
只因为它**不传 `idempotency_key`**，绕开了缓存路径。
所以这里必须证明：**把缓存写回校验之前，新测试会红。**

## 三个变异（每个都忠实复现一种真实退化）

  变异 1：`complete_structured` 不再传 `cache_result=False`
          → 复现**原始缺陷**（缓存写在校验之前）
  变异 2：`complete()` 忽略 `cache_result`，无条件写缓存
          → 同一缺陷的另一种改法（参数被架空）
  变异 3：校验通过后**不写**缓存
          → 复现"修过头"：幂等复用的正向价值被破坏
            （`test_structured_cache_holds_only_validated_output`
             的重放断言应当抓住它）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "src/storyforge/infra/llm/client.py"

TEST_ID = "tests/unit/test_llm_client.py"

# Windows 控制台默认 GBK，`✓`/`★` 会直接抛 UnicodeEncodeError 把脚本打死
# （不是"跑失败"，是压根没跑完）。与 `cli.py` 的 `_force_utf8_console` 同纪律。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 非 tty
            pass


def _run() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_ID,
         "-o", "addopts=--strict-markers --basetemp=.pytest_tmp"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-700:]


def _case(title: str, old: str, new: str) -> bool:
    """施加一处变异，跑测试，还原。

    ★ 锚点必须**逐字**匹配当前代码，否则 `assert` 会当场炸掉 ——
    这是刻意的：锚点漂移意味着"这个变异根本没被施加"，此时报"拦下"
    就是假证据。本项目已因"抓不到问题的检查"栽过三次（规则 3 / 规则 22 /
    `validate_map`）。
    """
    original = CLIENT.read_text(encoding="utf-8")
    assert old in original, (
        f"变异「{title}」的锚点在 client.py 中**找不到** —— 代码已漂移，"
        f"请同步更新本脚本的锚点后再跑。\n锚点原文：\n{old}"
    )
    mutated = original.replace(old, new, 1)
    assert mutated != original, f"变异「{title}」没有改动文件"
    CLIENT.write_text(mutated, encoding="utf-8")
    caught, out = _run()
    CLIENT.write_text(original, encoding="utf-8")      # 立即还原
    assert CLIENT.read_text(encoding="utf-8") == original, "还原失败！"

    print(f"  {'✓ 拦下' if caught else '★ 放行（测试无效）'}  {title}")
    if caught:
        for line in out.splitlines():
            if "assert" in line or "Error" in line:
                print(f"        {line.strip()[:100]}")
                break
    return caught


def main() -> int:
    results: list[bool] = []

    print("=" * 76)
    print("变异 1：`complete_structured` 恢复成「在校验前写缓存」（原始缺陷）")
    print("=" * 76)
    results.append(_case(
        "恢复原始缺陷（cache_result 不传）",
        "                json_mode=True, cache_result=False,\n",
        "                json_mode=True,\n",
    ))

    print()
    print("=" * 76)
    print("变异 2：`complete()` 忽略 `cache_result` 参数（架空它）")
    print("=" * 76)
    results.append(_case(
        "cache_result 被架空",
        "        if ctx.idempotency_key and cache_result:",
        "        if ctx.idempotency_key:",
    ))

    print()
    print("=" * 76)
    print("变异 3：校验通过后不写缓存（修过头 → 幂等复用失效）")
    print("=" * 76)
    results.append(_case(
        "校验通过也不写缓存",
        "            if ctx.idempotency_key and not result.from_cache:\n"
        "                self.idempotency.put(ctx.idempotency_key, raw)\n"
        "            return parsed, result",
        "            return parsed, result",
    ))

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, _ = _run()
    print(f"  {'★ 仍在失败（还原失败）' if ok else '全部测试通过 ✓'}")

    print()
    print("=" * 76)
    passed = sum(results)
    all_ok = passed == len(results) and not ok
    print(f"  结论：{passed}/{len(results)} 个变异被拦下"
          f"{'，且还原后通过 ✓' if all_ok else '　★ 不通过'}")
    print("=" * 76)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
