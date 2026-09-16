"""书 13 修复后仍有 3 条 continuity —— 判"是真硬切，还是匹配器仍不够"。

## 背景

连续性检查已改为复用 `_mentions`（主名拆分 + LCS≥4），
书 14/15/16 都因此改善，但书 13（都市言情）仍报 3 条。

本脚本读**当轮** debug 产物（新落盘通道），逐条给出机械判定依据。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from storyforge.config import get_settings  # noqa: E402
from storyforge.graph.nodes.detail_planner import load_plan, plan_from_dict  # noqa: E402
from storyforge.graph.nodes.skeleton_planner import (  # noqa: E402
    _MENTION_MIN_LCS,
    _lcs_len,
    _normalize_for_match,
)
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402

BID = 13
diag_p = pathlib.Path(f"doc/research/_diag_detail_{BID}.json")
if not diag_p.exists():
    raise SystemExit("没有诊断文件——请先跑 --diag")

diag = json.loads(diag_p.read_text(encoding="utf-8"))
store = ArtifactStore.from_settings(get_settings())

plans: dict[int, object] = {}
for no in range(1, (diag.get("expected") or 0) + 1):
    try:
        plans[no] = load_plan(store, BID, no)
    except Exception:  # noqa: BLE001
        pass
ref = diag.get("last_plans_ref") or ""
if ref:
    try:
        for k, v in json.loads(store.read_text(ref)).items():
            plans.setdefault(int(k), plan_from_dict(v))
    except Exception as exc:  # noqa: BLE001
        print(f"读 debug 产物失败：{exc}")

print("=" * 90)
print(f"书 {BID}：lint_issues 原文")
print("=" * 90)
for i in diag.get("lint_issues") or []:
    print(f"  {i}")

print()
print("=" * 90)
print("逐条机械判定（用现行匹配器复算）")
print("=" * 90)
for p in diag.get("adjacent_pairs") or []:
    a, b = p["exit_location"], p["entry_location"]
    if a == b or p.get("b_is_transition"):
        continue
    lcs = _lcs_len(_normalize_for_match(b), _normalize_for_match(a))
    print(f"\n第{p['a']}→{p['b']}章  trans={p.get('b_is_transition')} "
          f"beat={p.get('b_beat_type')}")
    print(f"  A = {a}")
    print(f"  B = {b}")
    print(f"  LCS = {lcs}（阈值 {_MENTION_MIN_LCS}）→ "
          f"{'判为同地点' if lcs >= _MENTION_MIN_LCS else '判为不同地点（报警）'}")
    # 场景级地点（本轮修复后才有的数据）
    pa, pb = plans.get(p["a"]), plans.get(p["b"])
    if pa is not None and pb is not None:
        print(f"  第{p['a']}章场景地点：{[s.location_name for s in pa.scenes]}")
        print(f"  第{p['b']}章场景地点：{[s.location_name for s in pb.scenes]}")

print()
print("=" * 90)
print("判定要点")
print("=" * 90)
print(
    "  · 若 LCS < 阈值 **且两个地方确实是不同场所** → 报警正确（模型漏置旗）\n"
    "  · 若 LCS < 阈值 **但实为同一场所** → 匹配器仍不够（但**不得直接调阈值**，\n"
    "     须先看它与'真换场'案例在字符串上是否可区分 —— 参考缺陷2的教训）\n"
    "  · 场景级地点（`scenes[].location_name`）是**新增数据**：\n"
    "     若同章内场景地点已覆盖两边，说明只是 entry/exit 措辞不同"
)
