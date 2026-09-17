"""把旧项目 `architect.scale_spec` 的篇幅档位导出为 `config/scales.toml`。

★ 一次性移植脚本。移植后 `config/scales.toml` 是**权威**。

实测发现：story 与 learn 的 fast/standard/grand 三档**完全相同**，
只有 `ultra` 不同（learn 的 ultra 讲知识点拓展路线，story 讲地图/对手扩张）。
因此 TOML 里只对 `ultra` 分模式写，其余共享 —— 避免两份重复数据漂移。

用法：`uv run python scripts/port_scales.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_BACKEND = Path(r"E:\AI_project\story_llm\backend")
OUT = ROOT / "config" / "scales.toml"

sys.path.insert(0, str(OLD_BACKEND))

#: 逐档位的字段顺序
_FIELDS = (
    "label", "desc", "chapters_lo", "chapters_hi", "vols_lo", "vols_hi",
    "per_vol_lo", "per_vol_hi", "segment", "typical_chapters", "expandable",
    "ending",
)
#: 长文本字段（单独放在表末尾，便于人读）
_TEXT_FIELDS = ("rule", "pacing")

_SCALES = ("fast", "standard", "grand", "ultra")


def _s(v) -> str:
    return json.dumps(str(v), ensure_ascii=False)


def _emit(scale: str, spec: dict, *, mode: str, indent: str = "") -> list[str]:
    lines = [f"{indent}[{mode}.{scale}]" if mode else f"{indent}[{scale}]"]
    for k in _FIELDS:
        if k not in spec:
            continue
        v = spec[k]
        if isinstance(v, bool):
            lines.append(f"{indent}{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            lines.append(f"{indent}{k} = {v}")
        else:
            lines.append(f"{indent}{k} = {_s(v)}")
    for k in _TEXT_FIELDS:
        if k in spec:
            lines.append(f"{indent}{k} = {_s(spec[k])}")
    return lines


def main() -> int:
    from app.agents.architect import scale_spec  # type: ignore[import-not-found]

    story = {s: scale_spec("story", s) for s in _SCALES}
    learn = {s: scale_spec("learn", s) for s in _SCALES}

    same = [s for s in _SCALES if story[s] == learn[s]]
    diff = [s for s in _SCALES if story[s] != learn[s]]
    print(f"两模式相同的档位：{same}")
    print(f"两模式不同的档位：{diff}")

    out: list[str] = [
        "# 篇幅档位（scale）—— 权威数据",
        "#",
        "# 移植自旧项目 `backend/app/agents/architect.py:scale_spec`。",
        "# 加载器：`src/storyforge/modes/scales.py`",
        "#",
        "# ★ 实测：story 与 learn 的 fast/standard/grand **完全相同**，",
        "#   只有 ultra 不同（learn 讲知识点拓展路线，story 讲地图/对手扩张）。",
        f"#   故共享档位写在顶层，仅 ultra 分模式写 —— 避免两份重复数据漂移。",
        "#   相同的档位：" + "、".join(same),
        "",
    ]

    out.append("# ── 共享档位（两模式相同）──")
    out.append("")
    for s in same:
        out.extend(_emit(s, story[s], mode=""))
        out.append("")

    out.append("# ── 分模式档位（两模式不同）──")
    out.append("")
    for s in diff:
        for mode in ("story", "learn"):
            spec = story[s] if mode == "story" else learn[s]
            out.extend(_emit(s, spec, mode=mode))
            out.append("")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"\n已写入 {OUT}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
