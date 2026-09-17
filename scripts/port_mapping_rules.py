"""把旧项目 `templates/scenes.py` 的 xianxia 平替修辞（10 条）导出为种子数据。

## 为什么需要"种子"

「平替修辞」是**每本书自创**的（`scenes.py:14` 明写"名称与阶梯必须按本书自创"），
所以它本应由 Agent 按知识点 + 流派生成。但旧项目那 10 条
（基础概念→灵根/入门心法 等）是**已验证可用**的映射范式，
适合作为：

  · **few-shot 示例**喂给生成平替修辞的 Agent（告诉它映射的粒度与风格）
  · **离线/首次运行**时的起点（无 key 时也能走完确认流程）

用法：`uv run python scripts/port_mapping_rules.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_BACKEND = Path(r"E:\AI_project\story_llm\backend")
OUT = ROOT / "config" / "mappings" / "xianxia_seed.toml"

sys.path.insert(0, str(OLD_BACKEND))


def _s(v) -> str:
    return json.dumps(str(v), ensure_ascii=False)


def main() -> int:
    from app.templates.scenes import SCENE_TEMPLATES  # type: ignore[import-not-found]

    tpl = SCENE_TEMPLATES["xianxia"]
    rules: list[str] = list(tpl["mapping_rules"])

    lines: list[str] = [
        f"# 平替修辞**种子**：{tpl['name']}（{tpl['id']}）",
        "#",
        "# 来源：旧项目 `backend/app/templates/scenes.py` 的 `mapping_rules`（10 条）。",
        "#",
        "# ★ 这是**种子/示例**，不是最终稿 —— 每本书的术语表必须自创",
        "#   （`scenes.py:14` 原文：名称与阶梯必须按本书自创）。",
        "#   用途：① 给生成平替修辞的 Agent 当 few-shot；② 无 key 时走通确认流程。",
        "#",
        "# 加载器：src/storyforge/modes/mappings.py",
        "",
        f"genre = {_s('xianxia')}",
        f"description = {_s(str(tpl.get('description', '')))}",
        f"style_prompt = {_s(str(tpl.get('style_prompt', '')))}",
        "",
        "# 平替修辞表（原术语 → 世界内说法）",
        "[[mappings]]",
    ]
    # 第一条紧跟表头，其余各起一段
    for i, rule in enumerate(rules):
        if i:
            lines.append("")
            lines.append("[[mappings]]")
        term, _, repl = rule.partition("→")
        lines.append(f"term = {_s(term.strip())}")
        lines.append(f"replacement = {_s(repl.strip())}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✓ 写入 {len(rules)} 条平替修辞 → {OUT.relative_to(ROOT)}")

    # 文风示例也一并留存（它们同样是有价值的 few-shot）
    samples = list(tpl.get("style_samples") or [])
    if samples:
        sp = ROOT / "config" / "mappings" / "xianxia_style_samples.md"
        sp.write_text(
            "# 文风示例（旧项目 `templates/scenes.py` 的 `style_samples`）\n"
            "#\n"
            "# ★ 种子数据：作为 learn writer 的 few-shot，"
            "告诉它「知识点化作功法/器物」是什么质感。\n"
            "# 每本书应当有自己的示例，这里只是起点。\n\n"
            + "\n\n---\n\n".join(samples) + "\n",
            encoding="utf-8",
        )
        print(f"✓ 写入 {len(samples)} 条文风示例 → {sp.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
