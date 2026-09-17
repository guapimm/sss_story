"""验证：现成打分卡对 learn（说明文）是否真的不可用。

审计结论声称「教学章的正确写法会被硬触发扣分」。这是决定
「learn 能否复用现成打分卡」的关键证据，必须自己复现。
"""

from __future__ import annotations

import inspect
import sys

sys.path.insert(0, "src")

from storyforge.scorecard.aggregate import evaluate

print("=" * 80)
print("① evaluate 的真实签名")
print("=" * 80)
print(f"  {inspect.signature(evaluate)}")

PROSE = """光合作用是植物利用光能，将二氧化碳和水转化为有机物并释放氧气的过程。
这个过程主要发生在叶绿体中。叶绿体含有叶绿素，能够吸收红光和蓝紫光。
光合作用分为光反应和暗反应两个阶段。光反应在类囊体薄膜上进行，产生ATP和NADPH。
暗反应在叶绿体基质中进行，利用ATP和NADPH固定二氧化碳，生成三碳糖。
综上所述，光合作用是生态系统能量流动的基础，也是大气氧气的主要来源。
因此理解光合作用，对认识生态系统的物质循环具有重要意义。
值得注意的是，光反应与暗反应虽然分工不同，但彼此依赖，缺一不可。
光反应为暗反应提供能量与还原剂，暗反应则为光反应补充ADP与NADP。
"""

print()
print("=" * 80)
print("② 喂一段标准说明文")
print("=" * 80)
try:
    r = evaluate(PROSE, global_no=1)
except TypeError as exc:
    print(f"  调用失败：{exc}")
    raise SystemExit(1)

for attr in ("score", "passed", "triggers"):
    print(f"  {attr:<10} = {getattr(r, attr, '<无此字段>')}")
print()
print("  其它可读字段：")
for name in dir(r):
    if name.startswith("_"):
        continue
    v = getattr(r, name)
    if isinstance(v, (int, float, str, bool)) and not callable(v):
        print(f"    {name:<22} = {v}")

print()
print("=" * 80)
print("③ 判定")
print("=" * 80)
trig = getattr(r, "triggers", []) or []
hard = [t for t in trig if "dialogue" in str(t) or "short" in str(t)]
print(f"  触发项：{trig}")
if not getattr(r, "passed", False):
    print("  ★ 未通过 —— 确认不可直接用于 learn")
if hard:
    print(f"  ★ 硬触发项：{hard}")
    print("     说明文天然没有对话（正确写法），却被 dialogue_floor 硬判失败；")
    print("     `综上所述` 是教学文的标准连接词，却在 CLICHES 里被当套话。")
    print("     → 这是**维度集合错配**，不是调参问题。")
