"""把旧项目的**两套** flow 数据移植到 `config/flows/`（两个命名空间）。

## 为什么分两个命名空间（用户决策）

旧项目有两套语义不同的 flow 数据，**不是同一回事**：

    flows.py        → learn 的 25 个**修仙流派**（scenes=['xianxia']）
                      23/25 从未被任何题材引用 —— 是 learn 专属的"流派轴"
    story_flows.py  → story 的 16 个**通用叙事套路**（channels + universal）
                      16/16 全被题材引用 —— 是 story 的"套路轴"

两者 **2 个 ID 撞名**（`rebirth` / `system`），且语义近似但措辞不同
（learn 版重生流是"以信息差**降维打击**"，story 版是"以信息差与成熟心智**改写命运**"）。
合并会丢掉模式专属措辞，也会让 learn 那 23 个未接线的混进 story 的引用域。

因此：

    config/flows/tropes/*.toml    ← story 16 个通用套路（已被 GenreCard.flows 引用）
    config/flows/xianxia/*.toml   ← learn 25 个修仙流派（可多选）

用法：`uv run python scripts/port_flows.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_BACKEND = Path(r"E:\AI_project\story_llm\backend")
OUT_TROPES = ROOT / "config" / "flows" / "tropes"
OUT_XIANXIA = ROOT / "config" / "flows" / "xianxia"

sys.path.insert(0, str(OLD_BACKEND))

#: 共有字段的写出顺序
_COMMON_ORDER = ("name", "hook", "structure", "protagonist",
                 "narrative_devices", "arc_example")


def _s(v) -> str:
    return json.dumps(str(v), ensure_ascii=False)


def _arr(items) -> str:
    return "[" + ", ".join(_s(str(x)) for x in items) + "]"


def _write_card(path: Path, card: dict, *, header: str, extra_order: tuple[str, ...]) -> None:
    """写一张卡。

    ⚠️ **不写 `id` 字段** —— id 由**文件名**给出（单一来源）。
    写进文件会产生"文件名 vs id 字段"的第二真相（本项目已记录过漂移教训）。
    """
    lines = [f"# {header}", "#",
             "# 移植自旧项目（权威来源，字段一一对应）。加载器：src/storyforge/modes/flows.py",
             "# ⚠️ id 由**文件名**给出，不在文件内重复声明。",
             ""]
    for key in (*_COMMON_ORDER, *extra_order):
        if key not in card:
            continue
        val = card[key]
        if isinstance(val, list):
            lines.append(f"{key} = {_arr(val)}")
        elif isinstance(val, bool):
            lines.append(f"{key} = {'true' if val else 'false'}")
        else:
            lines.append(f"{key} = {_s(val)}")
    # 未知字段原样保留（前向兼容）
    known = set(_COMMON_ORDER) | set(extra_order)
    for key in sorted(set(card) - known):
        val = card[key]
        lines.append(f"{key} = {_arr(val) if isinstance(val, list) else _s(val)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    from app.flows import FLOWS as LEARN  # type: ignore[import-not-found]
    from app.story_flows import STORY_FLOWS as STORY  # type: ignore[import-not-found]

    # ── story 16 通用套路 ──
    OUT_TROPES.mkdir(parents=True, exist_ok=True)
    for fid, card in STORY.items():
        _write_card(
            OUT_TROPES / f"{fid}.toml", card,
            header=f"通用叙事套路：{card.get('name', fid)}（story）",
            extra_order=("universal", "channels"),
        )
    print(f"✓ tropes/  {len(STORY)} 个 → {OUT_TROPES}")

    # ── learn 25 修仙流派 ──
    OUT_XIANXIA.mkdir(parents=True, exist_ok=True)
    for fid, card in LEARN.items():
        _write_card(
            OUT_XIANXIA / f"{fid}.toml", card,
            header=f"修仙流派：{card.get('name', fid)}（learn · 可多选）",
            extra_order=("scenes",),
        )
    print(f"✓ xianxia/ {len(LEARN)} 个 → {OUT_XIANXIA}")

    # ── 自检：两个命名空间的 ID 撞名应当**只在本脚本预期内** ──
    both = sorted(set(STORY) & set(LEARN))
    print(f"\n两套撞名的 ID（现分属不同命名空间，不再冲突）：{both}")
    print("合计写入", len(STORY) + len(LEARN), "张卡")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
