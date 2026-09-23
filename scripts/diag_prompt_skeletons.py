"""取证：style_critic 是否缺少 JSON 骨架（缺陷 11 的同类漏网）。

## 为什么要单独取证

2026-09-23 切到 `mimo-v2.6-flash` 后，真实 `write` 在 style_critic 崩：

    StructuredOutputError: 节点 style_critic 结构化输出校验失败（3 次尝试）：
      - 缺必填字段：issues.0.description  （×10 条）
    原始输出：{"score":6.5,"issues":[{"type":"AI味","location":"…",
              "detail":"…","fix":"…"}, ...]}

模型给的是 `detail` / `fix`，程序要的是 `description` / `suggestion`。
**信息全都在**，只是字段名不同。

## 这与缺陷 11 是同一根因

缺陷 11 的结论（交接文档 §0.6）：

    根因不在归一逻辑：抽取三节点 prompt **没有 JSON 骨架**（规划四节点有）。
    那份契约**只登记了 4 个规划节点，漏了 3 个抽取节点**
    → 契约全绿而缺陷存活。

本脚本要回答的是同一个问题：**现在还有哪些节点缺骨架？**
判据是确定性的（prompt 里有没有字段名），不依赖文本启发式。

用法：
    uv run python scripts/diag_prompt_skeletons.py
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "src" / "storyforge" / "infra" / "prompts"

#: 各节点期望的字段名（取自 schemas.py，人工登记以便核对）
EXPECTED: dict[str, tuple[str, ...]] = {
    "style_critic.md": ("description", "suggestion", "severity", "category"),
    "scene_extractor.md": ("state_updates", "entity", "field", "value"),
    "entity_resolver.md": ("canonical", "mentions"),
    "relation_extractor.md": ("source", "target", "kind"),
    "bible_architect.md": ("logline", "core_conflict"),
    "volume_planner.md": ("volume_no", "chapter_count"),
    "skeleton_planner.md": ("arcs", "segments"),
    "detail_planner.md": ("chapter_no", "scenes"),
}


def main() -> int:
    print("=" * 78)
    print("Prompt 骨架体检 —— 哪些节点的 prompt 给了字段名？")
    print("=" * 78)
    print()
    print(f"{'prompt 文件':<26}{'骨架?':<8}{'缺失的字段名'}")
    print("-" * 78)

    missing_files: list[str] = []
    no_skeleton: list[str] = []
    partial: list[str] = []

    for name, fields in EXPECTED.items():
        p = PROMPTS / name
        if not p.exists():
            missing_files.append(name)
            print(f"{name:<26}{'—':<8}★ 文件不存在")
            continue

        text = p.read_text(encoding="utf-8")
        absent = [f for f in fields if f not in text]

        # "有骨架" 的判据：出现 JSON 花括号结构（而不是只写"只输出 JSON"）
        has_brace = bool(re.search(r"\{\s*\"", text))
        has_all = not absent

        if has_brace and has_all:
            verdict = "✓ 有"
            no_skeleton.append("") if False else None
        elif has_brace:
            verdict = "△ 部分"
            partial.append(name)
        else:
            verdict = "★ 无"
            no_skeleton.append(name)

        print(f"{name:<26}{verdict:<8}{'、'.join(absent) if absent else '—'}")

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    if missing_files:
        print(f"  ★ 缺文件：{missing_files}")
    if no_skeleton:
        print(f"  ★★ 完全无骨架（最危险，模型自造字段名）：")
        for n in no_skeleton:
            print(f"        - {n}")
    if partial:
        print(f"  △ 有骨架但缺关键字段：")
        for n in partial:
            print(f"        - {n}")
    if not (missing_files or no_skeleton or partial):
        print("  ✓ 全部节点都有覆盖完整字段的骨架")

    print()
    print("  说明：判据是**确定性**的（prompt 里有没有那个字段名），")
    print("        不依赖文本启发式 —— 见交接文档 §4.8 的教训。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
