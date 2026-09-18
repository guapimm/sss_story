"""一次性替换：让契约测试用跨模块解析器（`_resolve_schema`）。

背景：`schemas.py` 已 791/800 行，schema 定义按职责分布在三个模块。
契约原先用 `getattr(schemas, name)` 单点解析 —— 那会让"把 schema 挪到
职责更合适的模块"被契约挡住，变相鼓励往 `schemas.py` 硬塞。

用法：`uv run python scripts/_migrate_skeleton_resolver.py`（一次性）
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tests/contract/test_prompt_skeletons.py"

OLD = (
    "    import storyforge.infra.llm.schemas as S\n"
    "\n"
    "    schema = getattr(S, schema_name)\n"
)
NEW = "    schema = _resolve_schema(schema_name)\n"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    n = text.count(OLD)
    if n == 0:
        print("未找到目标模式（可能已替换过）")
        return 1
    TARGET.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print(f"✓ 替换 {n} 处")
    left = TARGET.read_text(encoding="utf-8").count("getattr(S, schema_name)")
    print(f"  剩余 getattr(S, ...) ：{left}（应为 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
