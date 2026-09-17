"""复现 `scene_extractor` 的 **list[str] 型** state_updates 失败。

## 与既有诊断脚本的关系（不重复）

`scripts/diag_extract_shapes_all.py` 已覆盖 **4 种**形状，全部通过：
  1. `[{"entity","attribute","value"}]`
  2. `[{"person","attribute","from","to"}]`
  3. `{"李明": "情绪从疲惫变为恐惧"}`          ← dict，值是字符串
  4. `{"李明": {"情绪状态": "…"}}`              ← dict 套 dict

本次（阶段 1 验证真实链路时）实测发现**第 5 种**：

    "state_updates": ["林默：失忆", "林默：获得《住户须知》"]   ← **list[str]**

即"外层是 list、元素是字符串"。既有归一逻辑只处理
「外层是 dict」的情况（`normalize_record_list` 的入口判据是
`isinstance(v, dict)`），所以 list[str] **原样透传** → Pydantic 报
`Input should be an object` → 3 次重试全败 → **中断整个 write**。

## 本脚本的用途

离线复现该形状（不需要调 LLM），把"哪几种 list[str] 写法会崩"钉死，
作为修复的依据与回归基线。

用法：`uv run python scripts/diag_scene_list_shape.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storyforge.infra.llm.extraction_schemas import SceneExtractionOut  # noqa: E402

#: 实测/推演的 list[str] 写法（模型把"谁变了什么"写成一行文字）
CASES: dict[str, list[str]] = {
    "E1 冒号分隔": ["林默：失忆", "林默：获得《住户须知》"],
    "E2 中文冒号": ["林默：情绪从疲惫变为惊恐"],
    "E3 自然句": ["林默情绪从疲惫变为极度惊恐"],
    "E4 JSON 串元素": ['{"entity":"林默","value":"失忆"}'],
    "E5 破折号": ["林默——记忆缺失"],
    "E6 混合（既有 dict 又有 str）": [
        {"entity": "林默", "field": "记忆", "value": "失忆"},
        "王叔：死亡",
    ],
}


def main() -> int:
    base = {"summary": "本章摘要", "events": [{"description": "某事件"}]}
    print("=" * 76)
    print("list[str] 型 state_updates —— 当前实现是否接受")
    print("=" * 76)
    bad = 0
    for label, su in CASES.items():
        try:
            o = SceneExtractionOut(**base, state_updates=su)
            got = [(u.entity, u.field, u.value) for u in o.state_updates]
            print(f"  {label:<26} OK    -> {got}")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            first = str(exc).splitlines()[1].strip() if len(str(exc).splitlines()) > 1 else str(exc)[:60]
            print(f"  {label:<26} ★FAIL -> {first}")

    print()
    print("=" * 76)
    print(f"结论：{bad}/{len(CASES)} 种写法当前会**中断整个 write**")
    print("=" * 76)
    print("""
  判据：`normalize_record_list` 的入口是 `isinstance(v, dict)` ——
  外层是 **list** 时直接透传，元素若是 **str** 就到不了归一逻辑。

  修法（与既有一致：**归一，不拒绝**）：在 `_normalize_containers` 里
  对 list 的**元素**做一次归一 —— 元素是 str 时按
  `名：值` / `名:值` / `名——值` 拆成 `{entity, value}`，
  拆不出实体名时整条作为 `value` 保留（信息不得丢）。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
