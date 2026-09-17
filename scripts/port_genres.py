"""把旧项目 `story_genres.py` 的 18 个题材导出为 `config/genres/*.toml`。

★ 一次性移植脚本。移植后 `config/genres/` 是**权威**，旧项目只读参考。
（与 `diag_extract_*.py` 同类：取证/移植工具，非运行期代码。）

用法：`uv run python scripts/port_genres.py`
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_BACKEND = Path(r"E:\AI_project\story_llm\backend")
OUT_DIR = ROOT / "config" / "genres"

sys.path.insert(0, str(OLD_BACKEND))


def _toml_str(s: str) -> str:
    """TOML 基本字符串（转义反斜杠与引号）。

    ⚠️ 用 `json.dumps` 而非手写拼接：JSON 字符串语法是 TOML 基本字符串的
    子集（除 `\\uXXXX` 外），实测中文字符无需转义，安全。
    """
    return json.dumps(s, ensure_ascii=False)


def _toml_array(items) -> str:
    return "[" + ", ".join(_toml_str(str(x)) for x in items) + "]"


#: 字段顺序（与 `GenreCard` 一致，便于人读）
_ORDER = (
    "id", "name", "channel", "tags", "world_prompt", "growth_ladder",
    "naming", "tropes", "reader_expect", "tone_examples", "flows",
)


def main() -> int:
    from app.story_genres import GENRES  # type: ignore[import-not-found]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    seen_ids: set[str] = set()
    for g in GENRES:
        gid = str(g["id"])
        if gid in seen_ids:
            print(f"  ★ 重复 id：{gid}（跳过）")
            continue
        seen_ids.add(gid)

        lines: list[str] = [
            f"# 题材：{g.get('name', gid)}",
            "#",
            "# 移植自旧项目 `backend/app/story_genres.py`（权威来源，字段一一对应）。",
            "# 数据与代码分离：改题材不需要改 Python。",
            "# 加载器：`src/storyforge/modes/genres.py`",
            "",
        ]
        for key in _ORDER:
            if key not in g:
                continue
            val = g[key]
            if isinstance(val, list):
                lines.append(f"{key} = {_toml_array(val)}")
            else:
                lines.append(f"{key} = {_toml_str(str(val))}")
        # 未知字段原样保留（前向兼容）
        for key in sorted(set(g) - set(_ORDER)):
            val = g[key]
            if isinstance(val, list):
                lines.append(f"{key} = {_toml_array(val)}")
            else:
                lines.append(f"{key} = {_toml_str(str(val))}")

        (OUT_DIR / f"{gid}.toml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        written += 1
        print(f"  ✓ {gid:<20} {g.get('name', '')}"
              f"（flows={len(g.get('flows', []))}）")

    print(f"\n共写入 {written} 个题材 → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
