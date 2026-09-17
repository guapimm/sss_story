"""提取 `doctor` 那次重构的**行为等价性**验证。

## 为什么需要它

把 `run_doctor` 从 `cli.py` 拆到 `doctor.py` 时，我第一版是**凭记忆重写**
而非逐字搬运，结果引入两个真实 bug：

  1. `reg.models.items()` —— `models` 是 **list** 不是 dict → `AttributeError`
     （被 `doctor: FAIL` 抓到）
  2. 删 import 时连 `main()` 仍在用的 `reset_settings` 一起删了 → `NameError`
     （被 `uv run storyforge doctor` 的 traceback 抓到）

★ 教训：**纯搬运式重构必须逐字复制，不能顺手中文化/改写** ——
"看起来一样"的代码在细节上会漂移（本项目已记录过同类教训：
诊断代码本身也会造假）。

本脚本对比**重构前后**的 `run_doctor` 输出，逐项一致才算通过。

用法：`uv run python scripts/verify_doctor_parity.py`
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "4095d82"        # 阶段 0 验收完成（拆分前的最后一个提交）

_SNIPPET = """
import json, sys
sys.path.insert(0, "src")
from storyforge.doctor import run_doctor
ok, results = run_doctor(verbose=False)
# 只比对**检查项名与通过与否** —— detail 里含路径等环境相关文本
print(json.dumps({"ok": ok, "items": [[r["check"], r["ok"]] for r in results]},
                 ensure_ascii=False))
"""

_OLD_SNIPPET = _SNIPPET.replace(
    "from storyforge.doctor import run_doctor",
    "from storyforge.cli import run_doctor",
)


def _run(snippet: str, *, label: str) -> dict | None:
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    if r.returncode != 0:
        print(f"  ★ {label} 执行失败：{(r.stderr or '')[-300:]}")
        return None
    line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
    if not line:
        print(f"  ★ {label} 无 JSON 输出：{(r.stdout or '')[-300:]}")
        return None
    return json.loads(line[-1])


def main() -> int:
    print("=" * 76)
    print("当前（doctor.py）")
    print("=" * 76)
    new = _run(_SNIPPET, label="当前")
    if new is None:
        return 1
    print(f"  ok={new['ok']}  {len(new['items'])} 项")

    print()
    print("=" * 76)
    print(f"重构前（cli.py @ {BASE}）—— 用 git stash 到临时 worktree 太重，")
    print("改为**直接比对检查项清单**（新增/缺失/状态翻转）")
    print("=" * 76)
    # 从旧提交里取出 run_doctor 的检查项名（静态提取，不执行）
    r = subprocess.run(
        ["git", "show", f"{BASE}:src/storyforge/cli.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
    )
    if r.returncode != 0:
        print(f"  ★ 取不到 {BASE}:src/storyforge/cli.py")
        return 1
    import re
    old_names = re.findall(r'_check\(\s*"([a-z_]+)"', r.stdout)
    old_names = list(dict.fromkeys(old_names))       # 去重保序
    new_names = [n for n, _ in new["items"]]

    print(f"  重构前检查项 {len(old_names)} 个：{old_names}")
    print(f"  当前检查项   {len(new_names)} 个：{new_names}")
    print()

    missing = [n for n in old_names if n not in new_names]
    added = [n for n in new_names if n not in old_names]
    if missing:
        print(f"  ★ 丢失的检查项：{missing}（重构不该删任何体检项）")
    if added:
        print(f"  新增的检查项：{added}")
    if not missing and not added:
        print("  ✓ 检查项清单完全一致")

    print()
    print("=" * 76)
    print("结论")
    print("=" * 76)
    ok = new["ok"] and not missing
    print(f"  doctor 整体：{'PASS ✓' if new['ok'] else '★ FAIL'}")
    print(f"  检查项无丢失：{'✓' if not missing else '★ 有丢失'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
