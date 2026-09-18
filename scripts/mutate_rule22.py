"""变异测试：证明规则 22 能拦下**真实发生过**的运行时 NameError。

## 复现的目标缺陷（阶段 2-3 收尾实测）

`commands/learning.py` 的 `_gen_with_llm` 里用了 `get_settings()`
却**没 import**，于是走进 LLM 路径时：

    ⚠ LLM 生成失败（NameError: name 'get_settings' is not defined）

★ 这条缺陷**同时逃过了两道防线**：
  · 规则 21 只查**注解**（`from __future__ import annotations` 让注解惰性求值，
    那是它存在的理由）—— 函数体不在其内
  · 单测抓不到 —— 该函数**需要 API key 才会被调用**，测试里不会走进去

最终是**手工跑真实 CLI** 才暴露。规则 22 就是为这个缺口加的。

## ★★ 变异 1 曾**两次**自证了规则无效（教训）

**第一次**：锚点写的是 `"    from storyforge.config import get_settings\\n"`，
用 `str.replace(..., 1)` 删**第一个**匹配 —— 而该行在文件里出现**两次**：

  · `_has_api_key` 里的那个是**死导入**（该函数体根本没用它）
  · `_gen_with_llm` 里的才是**真依赖**（被 `get_settings()` 调用）

删掉死导入 → 不产生 NameError → 规则"没拦下"**是正确行为**，
错的是变异本身（违反"变异必须忠实复现真实改动"）。

**第二次（改对锚点后仍不报）**：暴露出规则 22 **真的失效**——
`_collect_bound(tree, ...)` 对整棵树 `ast.walk`，把"函数体内的 import"
当成**模块级**绑定，于是 `_gen_with_llm` 里删掉的 import 被
`_has_api_key` 里的同名 import "补上"了。⇒ 规则已重构为 `symtable` 实现。

★ 两次都说明同一件事：**变异测试的价值就在于它会拆穿"看起来对"的规则**。
若当初只跑 `pytest` 看绿灯就收工，这条契约会一直是个摆设。

## 四个真变异

  变异 1：删掉 `_gen_with_llm` **自己**的 import（按 AST 精确定位，不靠字符串）
          → 复现**原始缺陷** → 必须拦下（★ 这条曾证明规则失效）
  变异 2：删掉一个闭包依赖（外层函数的局部名）
          → 验证**闭包作用域**分析正确（初版误报来源）
  变异 3：删掉推导式目标变量
          → 验证**推导式作用域**分析正确（初版另一处误报来源）
  变异 4：把模块级 import 挪进**另一个函数**
          → 验证**跨函数不可见**（就是 H2 泄漏的直接反面）
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/storyforge/commands/learning.py"

TEST_ID = ("tests/contract/test_architecture_invariants.py::"
           "test_rule22_function_bodies_reference_defined_names")


def _run() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_ID, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "") + (r.stderr or "")


def _find_import_line(source: str, fn_name: str, symbol: str) -> int | None:
    """按 **AST** 找 `fn_name` 函数体内 `symbol` 的 import 行号；没有则 None。

    ★ 刻意不用字符串搜索：正是因为字符串锚点分不清
    `_has_api_key` 与 `_gen_with_llm` 里两个同名 import，变异才失准过一次。
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.ImportFrom)
                        and any(a.name == symbol for a in sub.names)):
                    return sub.lineno
    return None


def _case(title: str, mutate, *, expect: bool) -> bool:
    """跑一个变异：`expect=True` 表示**应当**被拦下。"""
    original = TARGET.read_text(encoding="utf-8")
    mutated = mutate(original)
    assert mutated != original, f"变异「{title}」没有改动文件"
    TARGET.write_text(mutated, encoding="utf-8")
    caught, out = _run()
    TARGET.write_text(original, encoding="utf-8")          # 立即还原
    assert TARGET.read_text(encoding="utf-8") == original, "还原失败！"

    ok = caught == expect
    verdict = "拦下 ✓" if caught else "放行"
    want = "应拦下" if expect else "应放行"
    print(f"  {'✓' if ok else '★'} {title}")
    print(f"      {verdict}（{want}）")
    if caught:
        for line in out.splitlines():
            if "未定义名" in line:
                print(f"      {line.strip()[:110]}")
                break
    elif expect:
        print("      ★ 规则无效：该缺陷未被发现")
    return ok


def _delete_line(lineno: int):
    def _mutate(source: str) -> str:
        lines = source.splitlines(keepends=True)
        del lines[lineno - 1]
        return "".join(lines)
    return _mutate


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    results: list[bool] = []

    print("=" * 76)
    print("变异 1：删掉 `_gen_with_llm` 自己的 get_settings import")
    print("        （复现阶段 2-3 真实发生过的 NameError；曾自证规则失效）")
    print("=" * 76)
    real = _find_import_line(src, "_gen_with_llm", "get_settings")
    dead = _find_import_line(src, "_has_api_key", "get_settings")
    assert real, "找不到 `_gen_with_llm` 里的 get_settings import（锚点失效）"
    print(f"  定位：`_gen_with_llm` 的 import 在行 {real}（真依赖）")
    if dead:
        print(f"        `_has_api_key` 也有一处（行 {dead}）—— 那正是本变异第一次的错锚点")
    else:
        print("        `_has_api_key` 的**死导入**已清掉（本变异第一次曾误删它）")
    results.append(_case("删 `_gen_with_llm` 自己的 import", _delete_line(real), expect=True))

    print()
    print("=" * 76)
    print("变异 2：闭包依赖被删 → 内层引用应变报错")
    print("=" * 76)
    print("  `_from_dict` 的 `raw` 是参数（内层无闭包），故用 `_load_map` 的 `ref`：")
    print("  它被 `store.read_text(ref)` 用；删掉该赋值后引用即无绑定。")
    print("  （放行闭包是**必须**的 —— 初版正是漏了它而误报 30+ 处）")
    results.append(_case(
        "闭包/局部绑定被删",
        lambda source: source.replace(
            "    ref = ArtifactStore.learning_map_key(book_id)\n", "", 1).replace(
            "store.read_text(ref)", "store.read_text(learning_map_key(book_id))", 1),
        expect=True,
    ))

    print()
    print("=" * 76)
    print("变异 3：把模块级 import 挪进**另一个函数**（H2 泄漏的直接反面）")
    print("=" * 76)
    print("  `json` 被 4 个函数用；把它挪进 `_do_history` 一个函数里，")
    print("  其余 3 个函数就该报错。旧实现会把函数内 import 当模块级绑定 → 放行。")
    results.append(_case(
        "模块级 import 挪进单个函数",
        lambda source: source.replace("import json\n", "", 1).replace(
            "def _do_history(args: argparse.Namespace) -> int:\n",
            "def _do_history(args: argparse.Namespace) -> int:\n    import json\n", 1),
        expect=True,
    ))

    print()
    print("=" * 76)
    print("变异 4：推导式体内引用的名字来源被删 → 应变报错")
    print("=" * 76)
    print("  `_list_revisions` 里 `store.list_keys(...)` 的推导式用 `store`（参数）；")
    print("  把 `store` 参数改名而推导式内不改 → 该引用无绑定。")
    print("  ★ 这同时验证：推导式**不是**独立作用域（PEP 709），")
    print("    它的名字要在外层作用域里找。")
    results.append(_case(
        "推导式体内名字失去绑定",
        lambda source: source.replace(
            "def _list_revisions(store: ArtifactStore, book_id: int)",
            "def _list_revisions(_renamed_store: ArtifactStore, book_id: int)", 1),
        expect=True,
    ))

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, out = _run()
    print(f"  {'★ 仍在失败（还原失败）' if ok else '规则 22 通过 ✓'}")

    print()
    print("=" * 76)
    done = [r for r in results if r is not None]
    passed = sum(done)
    all_ok = passed == len(done) and not ok and len(done) == 4
    print(f"  结论：{passed}/{len(done)} 个变异被正确处理"
          f"{'，且还原后通过 ✓' if all_ok else '　★ 不通过'}")
    print("=" * 76)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
