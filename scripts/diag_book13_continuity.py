"""逐条判定书 13 的三条 `detail:continuity` —— 真硬切，还是旗没置对？

## 数据（来自 `_diag_detail_13.json`，真实 lint 消息）

    第7章  林知许的律所办公室        → 市立医院急诊科
    第9章  顾晏的公寓 / 林知许的律所办公室 → 法院调解中心
    第10章 法院附近的咖啡店          → 多场景（市立医院急诊 / 林知许公寓）

## 与书 16 的对比（为什么这次**不能**简单放过）

书 16 的两条是"同一栋楼内换房间"（办公楼内移动），语义上很近；
书 13 这三条**跨越了真实的物理距离**：
  · 律所 → 医院急诊科      （城市内两处不同机构）
  · 公寓/律所 → 法院调解中心（不同机构）
  · 咖啡店 → 医院+公寓      （且下一章是**多场景**）

它们**不是**"同一场所内的正常移动"，而是**真正的换场** ——
按修法 C 的定义（场景层面换地方就要置 `is_transition`），
**模型应当置旗**。没置 → 报警是**正确的**。

## 本脚本给出机械判定依据（不靠印象）
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from storyforge.domain.outline import WorldStateSnapshot  # noqa: E402

CASES: list[tuple[int, str, str]] = [
    (7, "林知许的律所办公室", "市立医院急诊科"),
    (9, "顾晏的公寓 / 林知许的律所办公室", "法院调解中心"),
    (10, "法院附近的咖啡店", "多场景（市立医院急诊 / 林知许公寓）"),
]

print("=" * 84)
print("书 13 三条报警的机械判定")
print("=" * 84)
for no, a, b in CASES:
    shared = set(a) & set(b)
    # 只看**中文实词**的共享（去掉标点/空格/斜杠）
    import re  # noqa: PLC0415

    ta = set(re.findall(r"[\u4e00-\u9fa5]{2,}", a))
    tb = set(re.findall(r"[\u4e00-\u9fa5]{2,}", b))
    common = ta & tb
    print(f"\n第{no}章  {a}  →  {b}")
    print(f"  字符集交集大小 = {len(shared)}")
    print(f"  中文词交集     = {sorted(common) if common else '（无共享词）'}")
    print(f"  判定：", end="")
    if common:
        print(f"有共享词 {sorted(common)} → 可能同场所（需看细纲确认）")
    else:
        print("**无任何共享词** → 两个完全不同的场所 = **真换场**")

print()
print("=" * 84)
print("结论")
print("=" * 84)
print(
    "  三条报警**都是真换场**（跨机构，不是同楼换房间）。\n"
    "  按修法 C 已统一的口径（场景层面换地方就要置 `is_transition`），\n"
    "  模型**应当**置旗而未置 → **报警是正确的**。\n"
    "\n"
    "  → 这不是判据过严，是**模型漏置旗**。\n"
    "  → 按 D-16：书 13 是**单书特例**（只记录，不放宽判据）。\n"
    "\n"
    "  ⚠️ 但值得注意：书 11/15/16 都没有这个问题，只有书 13（都市言情）有 ——\n"
    "     可能与该题材『场景切换频繁、且多在生活场所间跳跃』有关。\n"
    "     若后续多书复现（≥3 本），再考虑在 prompt 里加强"
    "『生活场景切换也要置旗』的示例。"
)
