"""诊断：列出 src 里所有 `complete_structured(node="...")` 用的节点名，
并检查它们是否都在 `config/providers.toml` 的 `[nodes.*]` 里登记了。

## 为什么要做这件事

2026-09-18 实测：`storyforge learn-map gen --offline=false`（LLM 路径）
在**真实 MiMo key 已配置**的前提下失败：

    ProviderConfigError: 节点 'learning_map' 未在 providers.toml
    的 [nodes.*] 中声明能力需求。禁止在代码中直接指定模型（决策 D-09）。

也就是说：**写代码时新建了节点，但忘了在 providers.toml 登记能力**。
这条问题只在**真跑 LLM 路径**时才会暴露（离线/种子路径不经过路由器）。
"""

from __future__ import annotations

import ast
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # ① 代码里实际用到的节点名
    used: dict[str, str] = {}
    for p in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr == "complete_structured":
                    for kw in node.keywords:
                        if kw.arg == "node" and isinstance(kw.value, ast.Constant):
                            name = kw.value.value
                            used.setdefault(name, str(p.relative_to(ROOT)))

    # ② 配置里声明的节点
    cfg = tomllib.loads((ROOT / "config/providers.toml").read_text(encoding="utf-8"))
    declared = set((cfg.get("nodes") or {}).keys())

    print("=" * 74)
    print("代码中 complete_structured 用到的节点 vs providers.toml 已登记")
    print("=" * 74)
    miss = 0
    for name, src in sorted(used.items()):
        mark = "✓" if name in declared else "★ 未登记"
        if name not in declared:
            miss += 1
        print(f"  {mark:<10} {name:24}  （来自 {src}）")

    ghost = declared - set(used) - {
        "scorecard_engine", "health_scheduler", "continuity_checker", "structure_gate"}
    if ghost:
        print()
        print(f"  登记了但代码未使用（可能是零 LLM 明示，见判据）：{sorted(ghost)}")

    print()
    print("=" * 74)
    print(f"结论：{len(used)} 个代码节点，{miss} 个未登记")
    print("=" * 74)
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
