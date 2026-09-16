"""批次规模 → 超时的成本模型（用实测数据拟合，不外推）。

## 实测数据（book 16，卷 1，MiMo v2.5，reasoning_effort=high）

| 章数 | 耗时(s) | 每章(s) | completion | reasoning | 结果 |
|---|---|---|---|---|---|
| 1  | 48.2  | 48.2 | 1719 | 588  | OK |
| 3  | 115.8 | 38.6 | 4171 | 1263 | OK |
| 6  | 147.9 | 24.7 | 6671 | 1068 | lint 失败（非超时） |
| 15 | **>300（超时）** | – | – | – | **3 次全废，1135s** |

## 要回答的问题

1. 15 章到底需要多久？（外推）
2. 300s 的默认超时下，一批最多几章才**留得住余量**？
3. 批次大 vs 批次小，失败代价差多少？
"""

from __future__ import annotations

#: (章数, 耗时秒) —— 只取**成功**（未超时）的测量点
MEASURED: list[tuple[int, float]] = [(1, 48.2), (3, 115.8), (6, 147.9)]

#: 6 章那次是 lint 失败但**耗时可信**（LLM 正常返回、finish_reason=stop）
TIMEOUT_S = 300.0

print("=" * 84)
print("① 线性拟合：耗时 ≈ a + b × 章数")
print("=" * 84)
n = len(MEASURED)
sx = sum(c for c, _ in MEASURED)
sy = sum(t for _, t in MEASURED)
sxx = sum(c * c for c, _ in MEASURED)
sxy = sum(c * t for c, t in MEASURED)
b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
a = (sy - b * sx) / n
print(f"  固定开销 a ≈ {a:.1f}s（prompt 组装 + 首 token）")
print(f"  每章增量 b ≈ {b:.1f}s")
print()
print("  拟合值与实测对照：")
for c, t in MEASURED:
    pred = a + b * c
    print(f"     {c} 章：实测 {t:6.1f}s   拟合 {pred:6.1f}s   偏差 {pred - t:+.1f}s")

print()
print("=" * 84)
print("② 外推：15 章需要多久？（这正是 e2e 超时的那一批）")
print("=" * 84)
need15 = a + b * 15
print(f"  预测 15 章 ≈ {need15:.0f}s")
print(f"  默认超时 {TIMEOUT_S:.0f}s → 缺口 {need15 - TIMEOUT_S:+.0f}s")
print()
print(f"  ★ 与实测吻合：e2e 那轮 3 次尝试全部超时，共 1135s")
print(f"     （≈ 3 × 300s + 退避），符合「每章 ~{b:.0f}s × 15 章 > 300s」")

print()
print("=" * 84)
print("③ 在 300s 超时下，一批最多几章？（留余量，因为超时=整批作废）")
print("=" * 84)
print(f"  {'章数':<6}{'预测耗时':<12}{'占超时比':<11}{'评价':<20}")
for c in (3, 4, 5, 6, 8, 10, 15):
    pred = a + b * c
    share = pred / TIMEOUT_S
    if share <= 0.6:
        verdict = "✅ 安全（余量充足）"
    elif share <= 0.85:
        verdict = "⚠️ 偏紧"
    elif share <= 1.0:
        verdict = "❌ 无余量"
    else:
        verdict = "❌ 必然超时"
    print(f"  {c:<6}{pred:<12.0f}{share:<11.0%}{verdict}")

print()
print("=" * 84)
print("④ 失败代价：为什么『批次小』更好")
print("=" * 84)
print(
    "  超时 / lint 失败 = **整批作废**（失败批次不落盘）。\n"
    "  假设每批固定要花 T 秒，失败时这 T 秒的 token **全部白烧**。\n"
)
for c in (5, 15):
    pred = a + b * c
    print(f"  批 {c:>2} 章：一批 {pred:5.0f}s、{c:>2} 章的 token；"
          f"一批失败 = 白烧 {c:>2} 章的 token")
print()
print("  ★ 结论：批次越小，「一次失败」的损失越小；")
print("     而批次数量增加可由**并发**吸收（D-13 流水线本就是并行的）。")

print()
print("=" * 84)
print("⑤ 建议（数据推导，非拍脑袋）")
print("=" * 84)
print(f"  · 单批章数取 **5**：预测 {a + b * 5:.0f}s，占 300s 超时的 "
      f"{(a + b * 5) / TIMEOUT_S:.0%}，余量充足")
print(f"  · 原值 15 预测 {need15:.0f}s —— **超过超时上限**，必然 3 连败")
print("  · 15 章总量 = 3 批 × 5 章（并发跑，总墙钟 ≈ 1 批的时间）")
print()
print("  ⚠️ 这些数字是**单模型单轮实测**（MiMo v2.5 + effort=high）。")
print("     换模型/换 effort 需重测 —— 但**约束关系**不变：")
print("     `批次章数 × 每章耗时 + 固定开销` 必须显著小于超时上限。")
