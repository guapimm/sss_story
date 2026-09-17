"""变异测试：证明抽取形状归一的三处修复**真的**生效。

## 修的是什么（阶段 1 收尾实测，同一根因的三种表现）

真实 `write` 崩在 `scene_extractor`：

    StructuredOutputError: 结构化输出校验失败（3 次尝试）
    - `state_updates.0`：Input should be an object

深挖后发现是**两个**缺陷（第二个更危险）：

| # | 形状 | 现象 | 可见性 |
|---|---|---|---|
| F | `list[str]` | Pydantic 崩 → `write` 中断 | 可见 |
| G | `{"location": "+楼梯间"}` | 字段名被当成**实体名** | ★ **静默** |

## 三次真变异（改的都是被测试真正读取的代码）

  变异 1：撤掉 `list[str]` 通道（`normalize_string_entries` 不再被调用）
          → 模拟修复前 → 形状 F 的测试必须失败
  变异 2：撤掉形状 G 的判据（`_is_field_key` 恒 False）
          → 模拟修复前 → 语义反转测试必须失败
  变异 3：把 `deaths` 放回"拆条"字段集
          → 模拟我修 F 时**引入的真实回归** → 必须失败

**变异 3 特别重要**：它复现的是一次真实发生过、且当时单测全绿的回归 ——
只有用真实落盘的模型输出复验才抓到。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "src/storyforge/infra/llm/extraction_schemas.py"

T_SHAPE_F = ("tests/unit/test_schemas.py::"
             "test_scene_extraction_accepts_list_of_strings")
T_SHAPE_G = ("tests/unit/test_schemas.py::"
             "test_field_keyed_dict_is_not_read_as_entity")
T_REAL = ("tests/unit/test_schemas.py::"
          "test_real_captured_output_from_book19_validates")
T_PLAIN = ("tests/unit/test_schemas.py::"
           "test_plain_string_list_fields_are_not_split_into_records")

#: 变异 1：形状 F 的通道（validator 里那一行）
F_ANCHOR = "                d[f] = normalize_string_entries(val)\n"
#: 变异 2：形状 G 的判据
G_ANCHOR = '    return key.strip().lower() in _FIELD_KEY_ALIASES\n'
#: 变异 3：把 `deaths` 放进"拆条"字段集（复现我修 F 时的**真实回归**）。
#:
#: ⚠️ 第一版变异写成"把 `_PLAIN_STRING_LIST_FIELDS` 清空" —— **没被拦下**。
#: 因为清空只是"不再处理 deaths"，而真实回归是**把它挪进记录分组**
#: （于是 `["中年男人"]` 被拆成 `[{"value": "中年男人"}]`）。
#: 这说明变异必须**忠实复现当时的改动**，否则测的是一个不存在的场景
#: （本项目已记录过同类教训：变异不真 ⇒ 结论无效）。
RECORD_ANCHOR = '''_RECORD_LIST_FIELDS: tuple[str, ...] = (
    "state_updates", "injury_updates", "events", "entities",
)
'''
RECORD_MUTATED = '''_RECORD_LIST_FIELDS: tuple[str, ...] = (
    "state_updates", "injury_updates", "events", "entities",
    "deaths", "unsupported",
)
'''


def _run(test_id: str) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-700:]


def _mutate(old: str, new: str, *, label: str, test_id: str,
            results: list) -> None:
    print("=" * 76)
    print(label)
    print("=" * 76)
    src = SCHEMAS.read_text(encoding="utf-8")
    assert old in src, f"锚点未找到：{old.strip()[:50]}"
    SCHEMAS.write_text(src.replace(old, new, 1), encoding="utf-8")
    caught, out = _run(test_id)
    SCHEMAS.write_text(src, encoding="utf-8")          # 立即还原
    print(f"  {'拦下 ✓' if caught else '★ 没拦下（测试没测到这一环）'}")
    if not caught:
        print(out)
    results.append(caught)


def main() -> int:
    results: list[bool] = []

    # ---- 变异 1：list[str] 不再走拆条通道 ----
    _mutate(
        F_ANCHOR, "                pass  # 变异：撤掉 list[str] 通道\n",
        label="变异 1：撤掉 `list[str]` 归一通道（模拟修复前）\n"
              "        → 形状 F 应崩（Input should be an object）",
        test_id=T_SHAPE_F, results=results,
    )

    # ---- 变异 2：形状 G 判据失效 ----
    _mutate(
        G_ANCHOR, "    return False  # 变异：字段名判据失效\n",
        label="变异 2：让 `_is_field_key` 恒 False（模拟修复前）\n"
              "        → 字段名会被当成实体名（**静默语义反转**）",
        test_id=T_SHAPE_G, results=results,
    )

    # ---- 变异 3：复现我修 F 时引入的真实回归 ----
    _mutate(
        RECORD_ANCHOR, RECORD_MUTATED,
        label="变异 3：把 `deaths`/`unsupported` 挪进**拆条**字段集\n"
              "        → 复现**修 F 时引入的真实回归**（当时单测全绿，"
              "只有真实输出复验才抓到）",
        test_id=T_PLAIN, results=results,
    )

    # ---- 还原复验 ----
    print()
    print("=" * 76)
    print("还原后复验（含真实落盘输出那条）")
    print("=" * 76)
    oks = [_run(t)[0] for t in (T_SHAPE_F, T_SHAPE_G, T_REAL, T_PLAIN)]
    all_clear = not any(oks)
    print(f"  四条测试 {'★ 仍在失败（还原失败）' if not all_clear else '全部通过 ✓'}")

    print()
    print("=" * 76)
    for name, c in zip(("形状 F（list[str]）", "形状 G（字段名当实体）",
                        "deaths 分组回归"), results):
        print(f"  {'✓' if c else '★'} {name} 被拦下")
    print(f"  {'✓' if all_clear else '★'} 还原后复验通过")
    return 0 if (all(results) and all_clear) else 1


if __name__ == "__main__":
    raise SystemExit(main())
