"""架构不变量契约测试 —— 用 AST 把旧项目的教训变成**机器约束**。

## 为什么需要这个文件

旧项目有 47KB 的 `AGENTS.md` 写满了规约，但**靠人工遵守**，结果是：

| 规约 | 实际 |
|---|---|
| 「禁止在共享 pipeline 加 `is_story` 分支」 | 门面之外仍有 **30+ 处**直读 `book_kind` |
| 「`.env` 与模型配置不得冲突」 | `.env` 写模型被 JSON **静默覆盖** |
| 「测试必须隔离」 | **121/123** 个测试文件在模块级改 `os.environ` |
| 统一配置管理 | 26 个 JSON + 81 个 env + 7 个模块各自缓存 |

**人工遵守的规约不是规约。** 本文件把每条教训变成会失败的测试。

每条检查都注明它防的是**哪一次实测事故**，便于后人理解为什么不能删。
"""

from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _iter_py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names(tree: ast.Module) -> set[str]:
    """收集所有 import 的模块名（含 `from x import y` 的 x）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                names.add(node.module.split(".")[0])
            for a in node.names:
                names.add(a.name)
    return names


def _string_literals(tree: ast.Module) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((getattr(node, "lineno", 0), node.value))
    return out


def _attr_names(tree: ast.Module) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            out.append((getattr(node, "lineno", 0), node.attr))
    return out


# --------------------------------------------------------------------------- #
# 规则 1-2：分层依赖（框架不得侵入领域层）
# --------------------------------------------------------------------------- #


FRAMEWORK_MODULES = {"langgraph", "langchain", "langchain_core", "langchain_openai"}


def test_rule01_domain_has_no_framework_imports(src_root: Path) -> None:
    """规则 1：`domain/` 不得 import langgraph / langchain。

    防的是：框架侵入业务逻辑后无法替换、无法测试。
    验收标准（doc/07 结论）：`grep -r "langgraph" src/storyforge/domain/` 返回空。
    """
    offenders: list[str] = []
    for path in _iter_py(src_root / "domain"):
        bad = _imported_names(_parse(path)) & FRAMEWORK_MODULES
        if bad:
            offenders.append(f"{path.relative_to(src_root)}: {sorted(bad)}")
    assert not offenders, "domain/ 不得依赖 LLM 框架：\n  " + "\n  ".join(offenders)


def test_rule02_scorecard_has_no_llm_imports(src_root: Path) -> None:
    """规则 2：`scorecard/` 不得 import LLM 客户端 —— 打分卡必须**零 LLM 可复现**。

    防的是：质量打分变成不可复现的 LLM 判断（旧项目 Editor 分数
    被项目自己声明「不是小说质量真值」，却成了唯一的质量信号）。
    """
    offenders: list[str] = []
    for path in _iter_py(src_root / "scorecard"):
        names = _imported_names(_parse(path))
        bad = {n for n in names if n.startswith("storyforge.infra.llm")}
        bad |= names & FRAMEWORK_MODULES
        if bad:
            offenders.append(f"{path.relative_to(src_root)}: {sorted(bad)}")
    assert not offenders, "scorecard/ 必须零 LLM：\n  " + "\n  ".join(offenders)


def test_rule02b_domain_has_no_sqlalchemy(src_root: Path) -> None:
    """规则 2b：`domain/` 不得 import SQLAlchemy —— 领域逻辑不碰存储细节。"""
    offenders: list[str] = []
    for path in _iter_py(src_root / "domain"):
        bad = {n for n in _imported_names(_parse(path)) if n.startswith("sqlalchemy")}
        if bad:
            offenders.append(f"{path.relative_to(src_root)}: {sorted(bad)}")
    assert not offenders, "domain/ 不得依赖 SQLAlchemy：\n  " + "\n  ".join(offenders)


# --------------------------------------------------------------------------- #
# 规则 3：禁止模式分支
# --------------------------------------------------------------------------- #


#: 表示「模式」的**属性名 / 标识符**。
#:
#: ⚠️ 2026-09-17 加固：原集合只有 `book_kind` 系列，**没有 `mode`** ——
#: 而本项目自己的字段就叫 `Book.mode` / `settings.mode`
#: （`infra/storage/models.py:83`、`config.py:275`）。
#: 也就是说：**用自己推荐的字段名去判模式，原规则完全看不见。**
#: 实测 `if book.mode == "learn":` 在原规则下 CI 全绿。
MODE_BRANCH_TOKENS = {
    "book_kind", "is_story", "_is_story", "book_kind_",
    "mode", "book_mode", "modes",
}

#: 表示「模式取值」的**字符串字面量**。
#:
#: 比较的另一半：`== "story"` / `== "learn"`。
#: 单独出现（如 `mode: Literal["story","learn"]` 的声明）不该报，
#: 必须与**比较运算**同现才算分支 —— 见 `_mode_branch_hits`。
MODE_VALUE_LITERALS = {"story", "learn"}

#: 允许出现模式判断的模块（模式层自身 + 声明式配置）。
#:
#: `config.py` 里 `mode: Literal["story","learn"]` 是**类型声明**不是分支，
#: 但为保守起见仍靠 `_mode_branch_hits` 的"必须是比较"来放行，
#: 不在这里开白名单 —— 白名单越少越安全。
MODE_BRANCH_ALLOWED: frozenset[str] = frozenset()

#: 规则 3 的扫描范围。
#:
#: ⚠️ 2026-09-17 加固：原来只扫 `domain` + `graph/nodes`，
#: 而旧项目的教训恰恰是「门面外的直读散落在各处」
#: （`main.py` 路由层、`drift.py` 形参、`continue_story.py`）。
#: 因此改为**扫全部 `src/storyforge/`**，只有 `modes/` 是模式层、天然允许。
_MODE_SCAN_SKIP = ("modes",)


def _mode_branch_hits(tree: ast.Module) -> list[tuple[int, str]]:
    """找出**真正的模式分支**（比较运算），而不是"提到了 mode"。

    ## 为什么必须看比较、不能只看名字（实测的漏检）

    原实现只收集 `ast.Attribute.attr` 与字符串字面量，于是：

        if book.mode == "learn":        ← attr="mode"(不在旧集合) + "learn"(不在旧集合)
                                           → **测试通过**（漏检）

    修正后判据是**比较运算的一侧是模式属性、另一侧是模式字面量**，覆盖：

        book.mode == "learn"        settings.mode != "story"
        book.mode in ("learn",)     "learn" == book.mode
        book.mode.value == "learn"  BookMode.LEARN == book.mode

    ⚠️ 设计稿 `doc/01-总体架构.md:442` 用的正是 `ast.Compare` 形态，
    而原实现没有实现它 —— 这次把实现与设计稿对齐。
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue

        operands = [node.left, *node.comparators]
        # ① 该比较里有没有"模式属性"
        has_attr = False
        attr_name = ""
        for op in operands:
            for sub in ast.walk(op):
                if isinstance(sub, ast.Attribute) and sub.attr in MODE_BRANCH_TOKENS:
                    has_attr = True
                    attr_name = sub.attr
                    break
                if isinstance(sub, ast.Name) and sub.id in MODE_BRANCH_TOKENS:
                    has_attr = True
                    attr_name = sub.id
                    break
            if has_attr:
                break
        # ② 该比较里有没有"模式字面量"
        literals = [
            s for _, s in _string_literals_in(node)
            if s in MODE_VALUE_LITERALS
        ]
        # ③ 两者同现才算分支
        if has_attr and literals:
            hits.append((node.lineno, f"{attr_name} == {literals[0]!r}"))
        elif has_attr and any(
            isinstance(sub, ast.Attribute) and sub.attr == "value"
            for op in operands for sub in ast.walk(op)
        ):
            # `book.mode.value == something` 这类也拦（取值未必是字面量）
            hits.append((node.lineno, f"{attr_name}.value 参与比较"))
    return hits


def _string_literals_in(node: ast.AST) -> list[tuple[int, str]]:
    """只收集 `node` 子树内的字符串字面量。"""
    out: list[tuple[int, str]] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append((getattr(sub, "lineno", 0), sub.value))
    return out


def test_rule03_no_mode_branching(src_root: Path) -> None:
    """规则 3：**全 `src/` 共享代码**不得直接判模式，必须经 `modes/` Protocol。

    防的是旧项目「门面建成但门面外仍有 **30+ 处**直读 `book_kind`」：
    `main.py:1304` 路由层绕过门面、`main.py` 7 处 `kind=="story"`、
    `drift.py` 4 处 `is_story` 形参、`architect.py:2895/2916` 裸 `_flow.kind`、
    `continue_story.py` 6 处 `book.book_kind`。

    ## 2026-09-17 加固（三处漏检，实测确认）

    | 漏洞 | 后果 |
    |---|---|
    | `MODE_BRANCH_TOKENS` 不含 `mode` | 用**本项目自己的字段名**判模式 → 全绿 |
    | 不含 `story`/`learn` 字面量 | `== "learn"` 的那一半看不见 |
    | 只扫 `domain` + `graph/nodes` | 路由层 / infra / cli 的直读漏检 |
    | 只看名字不看比较（设计稿要 `ast.Compare`）| 实现与 `01-总体架构.md:442` 不一致 |

    四条一起修：集合补全 + 扫全 `src/` + 按**比较运算**判定。
    `modes/` 自身允许（它就是模式层）。
    """
    offenders: list[str] = []
    scanned = 0
    for path in _iter_py(src_root):
        rel = path.relative_to(src_root)
        if rel.parts and rel.parts[0] in _MODE_SCAN_SKIP:
            continue  # 模式层本身
        if str(rel) in MODE_BRANCH_ALLOWED:
            continue
        scanned += 1
        hits = _mode_branch_hits(_parse(path))
        if hits:
            detail = ", ".join(f"L{n}:{d}" for n, d in hits)
            offenders.append(f"{rel}: {detail}")

    # 自检：必须真的扫到文件，否则规则会**静默失效**（比报错更糟）
    assert scanned > 0, (
        "规则 3 没有扫到任何文件 —— 路径或跳过条件可能已过期，"
        "本规则会静默失效"
    )
    assert not offenders, (
        "共享代码不得判模式（应经 modes/ Protocol，见决策 D-08）：\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 4：禁止硬编码模型名
# --------------------------------------------------------------------------- #


def test_rule04_no_hardcoded_model_names(src_root: Path) -> None:
    """规则 4：不得出现 `model == "..."` 这类硬编码模型判断。

    防的是旧项目「思考预算策略不具跨模型鲁棒性」：
    同一套 prompt 下 mimo 空输出 **0%**，而 qwen3.8-flash **91.4%**。
    模型差异必须由 `config/providers.toml` 的能力声明驱动（决策 D-09）。

    同时防：DeepSeek 模型名已过期（`deepseek-chat` 2026-07-24 停服）。
    """
    known = re.compile(r"^(deepseek|gpt|claude|qwen|glm|mimo|gemini|o[13])", re.I)
    offenders: list[str] = []
    for path in _iter_py(src_root):
        if "providers" in path.name or "capability" in path.name:
            continue
        for lineno, text in _string_literals(_parse(path)):
            s = text.strip()
            # 只关心"看起来像模型 id"且出现在比较语境里的短字符串
            if len(s) <= 40 and known.match(s) and ("-" in s or "." in s):
                offenders.append(f"{path.relative_to(src_root)}:{lineno} {s!r}")
    assert not offenders, (
        "不得硬编码模型名（应走能力声明路由）：\n  " + "\n  ".join(offenders)
    )


import re  # noqa: E402  (放在规则 4 前使用，保持规则顺序可读)


# --------------------------------------------------------------------------- #
# 规则 5：State 不得含大文本
# --------------------------------------------------------------------------- #


def test_rule05_graph_state_has_no_large_text_fields(src_root: Path) -> None:
    """规则 5：`graph/state.py` 的 State 字段名不得暗示大文本。

    防的是 LangGraph checkpoint 膨胀 —— 官方承认 200 轮可达 **5.3 GB**，
    社区实测 32 线程 **1.24 GB**。旧项目同构问题：把 400 章 outline
    塞进 `books.outline` 单列。

    正文/细纲 JSON 必须存外部，State 只持引用（`*_ref`）。
    """
    state_file = src_root / "graph" / "state.py"
    if not state_file.exists():
        pytest.skip("graph/state.py 尚未创建")
    banned = ("content", "prose", "text", "body", "outline_json", "bible")
    tree = _parse(state_file)
    offenders: list[str] = []
    for node in ast.walk(tree):
        # 形如  content: str   /  content: str | None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if name.endswith("_ref") or name.endswith("_hash"):
                continue
            if any(b == name or name.endswith("_" + b) for b in banned):
                offenders.append(f"line {node.lineno}: {name}")
    assert not offenders, (
        "State 不得含大文本字段（应用 *_ref 引用）：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 6：外键与 PRAGMA
# --------------------------------------------------------------------------- #


def test_rule06_all_tables_have_foreign_keys(db) -> None:
    """规则 6：除根表外，每张表必须有 FOREIGN KEY，且必须被强制。

    防的是旧项目 **0 个 FK** → 删书靠人工清单 `BOOK_SCOPED_MODEL_NAMES`，
    注释自承**历史上漏过 6 张表**、生产产生 trace 孤儿行。

    同时验证 SQLite **默认不强制** FK，必须靠 `PRAGMA foreign_keys=ON`。
    """
    root_tables = {"books"}
    tables = [t for t in db.list_tables() if not t.startswith("sqlite_")]
    missing: list[str] = []
    for t in tables:
        if t in root_tables:
            continue
        if not db.foreign_key_targets(t):
            missing.append(t)
    assert not missing, f"以下表没有 FK：{missing}"
    assert db.foreign_keys_enabled() is True, "PRAGMA foreign_keys 必须为 ON"


# --------------------------------------------------------------------------- #
# 规则 7：章号类型
# --------------------------------------------------------------------------- #


def test_rule07_chapter_number_is_single_global_int(db) -> None:
    """规则 7：章号必须是单一全书递增 `int`，不得有卷内号列。

    防的是旧项目 P0 事故：`since_ch` 写入侧存**卷内号**、消费侧按**全局号**
    比对 → 「已持续 10011 章」→ 每个伤每章都挂「本轮必须推进」
    → Editor 每章判低分（4~5 分）→ 必改失败。

    决策 D-15：卷内序号派生不存储。
    """
    import sqlalchemy as sa

    insp = sa.inspect(db.engine)
    cols = {c["name"]: str(c["type"]) for c in insp.get_columns("chapters")}
    assert "global_no" in cols, "chapters 必须有 global_no"
    assert "INTEGER" in cols["global_no"].upper(), cols["global_no"]
    banned = {"chapter_no", "idx", "chapter_index", "local_no", "volume_local_no"}
    overlap = banned & set(cols)
    assert not overlap, f"不得再存卷内号列（会与 global_no 漂移）：{overlap}"


# --------------------------------------------------------------------------- #
# 规则 8：枚举单一来源
# --------------------------------------------------------------------------- #


def test_rule08_enums_defined_once(src_root: Path) -> None:
    """规则 8：枚举必须只有一个定义处（`domain/enums.py`）。

    防的是旧项目 severity 三方不一致：
      · `memory.py` 阈值表 5 档
      · `post_processor.py` prompt 3 档
      → 「轻微」「中」永不产生 → **最重的伤恰恰是唯一不被催的**（注意力反向引导）
    """
    enum_files = [
        p for p in _iter_py(src_root)
        if "enums" not in p.name and "providers" not in p.name
    ]
    offenders: list[str] = []
    for path in enum_files:
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(b) for b in node.bases}
                if any("Enum" in b for b in bases):
                    offenders.append(f"{path.relative_to(src_root)}:{node.lineno} {node.name}")
    assert not offenders, (
        "枚举必须集中定义在 domain/enums.py：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 9：禁止业务实例命名
# --------------------------------------------------------------------------- #


def test_rule09_no_business_instance_named_modules(src_root: Path) -> None:
    """规则 9：模块名不得含具体书籍/实例编号。

    防的是旧项目 `book23_ultra_repair.py` / `book25_ultra_repair.py`
    —— 以业务实例命名的**生产模块**，无法维护也无法删除。
    """
    pattern = re.compile(r"(book|novel|vol|volume)\d+", re.I)
    offenders = [
        str(p.relative_to(src_root)) for p in _iter_py(src_root) if pattern.search(p.stem)
    ]
    assert not offenders, f"模块名不得含业务实例编号：{offenders}"


# --------------------------------------------------------------------------- #
# 规则 10：文件与函数规模
# --------------------------------------------------------------------------- #


MAX_FILE_LINES = 800
MAX_FUNC_LINES = 200


def test_rule10_file_and_function_size_limits(src_root: Path) -> None:
    """规则 10：单文件 ≤800 行、单函数 ≤200 行。

    防的是旧项目 `pipeline.py` **8,607 行**、`_commit_chapter` 单函数
    **1,239 行**（项目自己在 ARCHITECTURE_MAP.md 中承认是结构债）。
    """
    file_offenders: list[str] = []
    func_offenders: list[str] = []
    for path in _iter_py(src_root):
        text = path.read_text(encoding="utf-8")
        n_lines = text.count("\n") + 1
        if n_lines > MAX_FILE_LINES:
            file_offenders.append(f"{path.relative_to(src_root)}: {n_lines} 行")
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                span = (node.end_lineno or node.lineno) - node.lineno + 1
                if span > MAX_FUNC_LINES:
                    func_offenders.append(
                        f"{path.relative_to(src_root)}:{node.lineno} {node.name} = {span} 行"
                    )
    assert not file_offenders, "文件过长：\n  " + "\n  ".join(file_offenders)
    assert not func_offenders, "函数过长：\n  " + "\n  ".join(func_offenders)


# --------------------------------------------------------------------------- #
# 规则 11：测试不得改环境变量
# --------------------------------------------------------------------------- #


def test_rule11_tests_do_not_mutate_environ_at_module_level(repo_root: Path) -> None:
    """规则 11：测试文件**不得在模块级**修改 `os.environ`。

    防的是旧项目 **121/123 个测试文件**这么做 → 单进程 pytest 全部用例
    跑在「最后被 import 的模块」留下的环境上 → 实测产生**假失败**
    （3 failed + 8 errors，逐文件单跑则全过）。

    本项目用 `config.configure()` 做运行时注入，不需要碰环境变量。
    """
    offenders: list[str] = []
    for path in _iter_py(repo_root / "tests"):
        tree = _parse(path)
        for node in tree.body:  # 只看**顶层**语句
            if isinstance(node, ast.Assign):
                src = ast.unparse(node)
                if "os.environ" in src:
                    offenders.append(f"{path.name}:{node.lineno} {src[:80]}")
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                src = ast.unparse(node.value)
                if "os.environ" in src and ("setdefault" in src or "update" in src):
                    offenders.append(f"{path.name}:{node.lineno} {src[:80]}")
    assert not offenders, (
        "测试不得在模块级改 os.environ（请用 config.configure）：\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 12：配置只从 Settings 读
# --------------------------------------------------------------------------- #


def test_rule12_no_scattered_environ_reads(src_root: Path) -> None:
    """规则 12：业务代码不得散落 `os.environ` 直读（除 providers 的 key 解析）。

    防的是旧项目 26 个 JSON + 81 个环境变量 + 7 个模块各自缓存，
    且 `.env` 写模型被 `llm_providers.json` **静默覆盖**。

    ⚠️ 用 **AST** 判定而不是子串匹配：子串匹配会把**文档里提到
    `os.environ` 的说明文字**也判为违规（第一次运行即误报），
    而误报会诱使人删掉解释性注释 —— 那正是旧项目"教训失传"的路径。
    """
    allowed_files = {"capability.py"}  # 只允许在能力层解析 API key 环境变量
    offenders: list[str] = []
    for path in _iter_py(src_root):
        if path.name in allowed_files or path.name == "config.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            # 形如 os.environ  /  os.environ.get(...)  /  os.getenv(...)
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                base = ast.unparse(node.value)
                if base.split(".")[-1] == "os":
                    offenders.append(f"{path.relative_to(src_root)}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr == "getenv":
                base = ast.unparse(node.value)
                if base.split(".")[-1] == "os":
                    offenders.append(f"{path.relative_to(src_root)}:{node.lineno}")
    assert not offenders, (
        "不得散落直读 os.environ（请用 config.get_settings）：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 13-16：LangGraph 生产坑（官方 open issue 实测）
# --------------------------------------------------------------------------- #


def test_rule13_no_inplace_state_mutation(src_root: Path) -> None:
    """规则 13：节点不得原地修改 state 对象。

    防的是 `langgraph#8314`：状态通道**引用透传** —— 普通节点原地改 state
    会**污染调用方输入**（静默数据损坏，无异常）。
    """
    offenders: list[str] = []
    for path in _iter_py(src_root / "graph" / "nodes"):
        tree = _parse(path)
        for node in ast.walk(tree):
            # 形如 state["x"] = ...  /  state.x = ...
            if isinstance(node, ast.Assign) and node.targets:
                for t in node.targets:
                    src = ast.unparse(t)
                    if src.startswith("state[") or src.startswith("state."):
                        offenders.append(f"{path.name}:{node.lineno} {src}")
    assert not offenders, (
        "节点不得原地改 state（须返回新对象或用 reducer）：\n  " + "\n  ".join(offenders)
    )


def test_rule15_timestamps_are_utc_iso_strings(src_root: Path) -> None:
    """规则 15：时间字段必须是 UTC ISO8601**字符串**，不得用 datetime 过 checkpoint。

    防的是 `langgraph#8826`：datetime **静默丢时区**。
    """
    offenders: list[str] = []
    for path in _iter_py(src_root):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id.endswith("_at") and not node.target.id.endswith("_ref"):
                    ann = ast.unparse(node.annotation)
                    if "datetime" in ann.lower() and "str" not in ann.lower():
                        offenders.append(f"{path.name}:{node.lineno} {node.target.id}: {ann}")
    assert not offenders, (
        "时间字段须为 str（UTC ISO8601）：\n  " + "\n  ".join(offenders)
    )


def test_rule16_llm_nodes_have_idempotency_key(src_root: Path) -> None:
    """规则 16：**调用 LLM 的**节点必须有幂等键。

    防的是 `langgraph#7417`（53 条评论仍 open）：**>180s 的调用会被静默重放**
    → 2~3× 冗余成本。而旧项目实测 `editor` 平均 **117s**、`architect` **72s**，
    章节生成极易超阈值。

    ## ★ 判定口径：按"是否真的调用 LLM"，不是按"是否住在 nodes/ 目录"

    原实现只看 `nodes/` 下有没有 `def` —— 于是**纯领域规则模块**
    （如 `continuity.py`：输入输出都是领域对象、零 LLM 调用）
    也会被要求使用幂等键，而它根本没有可幂等的调用。

    把它加个无意义的 `idempotency_key` 参数只是**取悦测试**；
    正确的做法是**让规则检查它真正关心的东西**：
    这个模块里有没有发起 LLM 调用。
    """
    nodes_dir = src_root / "graph" / "nodes"
    if not nodes_dir.exists() or not list(_iter_py(nodes_dir)):
        pytest.skip("尚无节点实现")

    #: 出现这些名字即认为该模块会发起 LLM 调用
    llm_markers = ("complete_structured", "client.complete", "_client.complete",
                   "LLMClient", "CallContext")

    offenders: list[str] = []
    checked = 0
    for path in _iter_py(nodes_dir):
        text = path.read_text(encoding="utf-8")
        if "def " not in text:
            continue
        if not any(m in text for m in llm_markers):
            continue  # 纯领域模块，无可幂等的调用
        checked += 1
        if "idempotency_key" not in text and "idem" not in text:
            offenders.append(str(path.relative_to(src_root)))

    assert checked > 0, (
        "没有任何模块被识别为 LLM 节点 —— 标记词可能已过期，"
        "本规则会静默失效（比报错更糟）"
    )
    assert not offenders, (
        "LLM 节点必须使用幂等键（防静默重放）：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 17：信号必须有消费者（★ 旧项目最严重的单点缺陷）
# --------------------------------------------------------------------------- #


def test_rule17_no_write_only_signals(src_root: Path) -> None:
    """规则 17：每个写出的信号必须有读取点。

    ★ 防的是旧项目 **`must_review` 是只写不读的死信号**：
    全仓 **25 个命中、0 个读取点**（唯一近似读取是把它写进 trace metadata）。
    → `style_metrics.needs_rewrite` 的全部"阻断"语义，
      实际强度 = 强制调用 Editor 一次 + 加一段警告；
      Editor 返回 `revised_content: null` 则**正文一字不改入库**。
    → 生产实测：**185/210 章（88.1%）**被点名文风问题，其中 **57 章仍判 passed**。

    检测范围：**模块级**的 `must_*` / `needs_*` / `should_*` / `require_*` 绑定。
    排除项（避免误报）：
      · 枚举成员（`class Foo(StrEnum)` 内的赋值）—— 它们是命名常量，不是信号
      · 函数参数与局部变量 —— 它们在同一函数内被使用；本检查针对模块级信号
      · 被 `__all__` 导出的名字 —— 供外部消费
    """
    suspicious = re.compile(r"^(must_|needs_|should_|require_)", re.I)
    offenders: list[str] = []

    for path in _iter_py(src_root):
        tree = _parse(path)

        # 收集「枚举类内部的赋值」—— 它们是常量定义，不属于信号
        enum_member_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(b) for b in node.bases}
                if any("Enum" in b for b in bases):
                    for sub in node.body:
                        if isinstance(sub, ast.Assign):
                            enum_member_lines.add(sub.lineno)
                        elif isinstance(sub, ast.AnnAssign):
                            enum_member_lines.add(sub.lineno)

        # 只扫**模块级**语句（函数内的局部变量不在此列）
        defined: dict[str, int] = {}
        for node in tree.body:
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for t in targets:
                if (
                    isinstance(t, ast.Name)
                    and suspicious.match(t.id)
                    and node.lineno not in enum_member_lines
                ):
                    defined[t.id] = node.lineno

        if not defined:
            continue

        # 全文件范围内查找「被消费」：条件判断、返回值、函数调用参数、容器成员
        consumed: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.If | ast.While | ast.Assert):
                for n in ast.walk(node.test):
                    if isinstance(n, ast.Name):
                        consumed.add(n.id)
            elif isinstance(node, ast.Return) and node.value is not None:
                for n in ast.walk(node.value):
                    if isinstance(n, ast.Name):
                        consumed.add(n.id)
            elif isinstance(node, ast.Call):
                for a in list(node.args) + [k.value for k in node.keywords]:
                    for n in ast.walk(a):
                        if isinstance(n, ast.Name):
                            consumed.add(n.id)

        exported = {s for _, s in _string_literals(tree)}
        dead = sorted(n for n in defined if n not in consumed and n not in exported)
        if dead:
            offenders.append(f"{path.relative_to(src_root)}: {dead}")

    assert not offenders, (
        "存在只写不读的死信号（旧项目 must_review 的复现，25 处写入 0 处读取）：\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 18：prompt 要求必须有校验器
# --------------------------------------------------------------------------- #


def test_rule18_prompt_requirements_have_validators(src_root: Path) -> None:
    """规则 18：prompt 中的数量/结构要求必须有对应校验器。

    防的是旧项目「要求 ≠ 执行」的系统性失效：
      · `entry_state` 填充率 **仅 13%**（段1 100% → 段3起 0%）
      · `milestone_ids` 落地率 **0%**
      · `plot_detail` 下限 900，实测 p50 **788**
      · `conflict_curve` / `character_arcs` **从不校验、不落库**

    实现：扫描 prompts 目录中的数量约束（「≥N 个 X」「N~M 字」），
    断言 domain/validators 中存在同名校验。
    """
    prompts_dir = src_root / "infra" / "prompts"
    validators_dir = src_root / "domain" / "validators"
    if not prompts_dir.exists() or not list(prompts_dir.glob("*.md")):
        pytest.skip("尚无 prompt 模板")
    validator_text = "\n".join(
        p.read_text(encoding="utf-8") for p in _iter_py(validators_dir)
    )
    numeric = re.compile(r"[≥>=]\s*(\d+)\s*(?:个|条|行|处|次)")
    offenders: list[str] = []
    for p in prompts_dir.glob("*.md"):
        for m in numeric.finditer(p.read_text(encoding="utf-8")):
            # 宽松匹配：校验器中应出现同一个数字
            if m.group(1) not in validator_text:
                offenders.append(f"{p.name}: 要求「{m.group(0)}」但校验器中找不到 {m.group(1)}")
    assert not offenders, (
        "prompt 要求缺少校验器（要求≠执行的教训）：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 19-20：checkpoint 与大产物
# --------------------------------------------------------------------------- #


def test_rule19_no_prose_in_graph_state(src_root: Path) -> None:
    """规则 19：正文/细纲 JSON 不得进入图 State。

    防的是 LangGraph checkpoint 膨胀（官方 200 轮 **5.3 GB** / 社区 1.24 GB）。
    State 只能持 `*_ref` 引用。

    ## 判定口径

    只看**文本类型**的字段。整数/计数类（`prose_chars`、`prose_hash`）
    **是允许的** —— 它们是小标量，不造成膨胀，且对观测有用。
    （初版把 `prose_chars` 误判为违规，是规则本身太宽。）
    """
    state_file = src_root / "graph" / "state.py"
    if not state_file.exists():
        pytest.skip("graph/state.py 尚未创建")
    tree = _parse(state_file)
    banned = ("prose", "chapter_content", "plan_json", "bible_text", "content")
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if name.endswith(("_ref", "_hash", "_chars", "_len", "_id", "_no")):
                continue
            ann = ast.unparse(node.annotation).lower()
            # 仅当字段可能是大文本时才算违规
            is_texty = "str" in ann or "dict" in ann or "any" in ann
            if is_texty and any(b in name for b in banned):
                offenders.append(f"line {node.lineno}: {name}: {ann}")
    assert not offenders, (
        f"State 不得含正文/大文本字段（应用 *_ref 引用）：{offenders}"
    )


def test_rule20_no_langsmith_hard_requirement(src_root: Path) -> None:
    """规则 20：无 `LANGSMITH_*` 环境变量时系统必须完全可用。

    防的是 SaaS 锁定。用户明确要求「模型测评 trace 后续再加，可以先预留」，
    因此一期只建表与接口，**不接任何 SaaS**。
    """
    offenders: list[str] = []
    for path in _iter_py(src_root):
        if "langsmith" in path.read_text(encoding="utf-8") and "observability" not in str(path):
            offenders.append(str(path.relative_to(src_root)))
    assert not offenders, (
        "不得硬依赖 langsmith（无该环境变量时必须可用）：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 21：注解里的名字必须真的存在
# --------------------------------------------------------------------------- #


def test_rule21_annotations_reference_defined_names(src_root: Path) -> None:
    """规则 21：**注解里引用的名字必须真的存在**（防"潜伏的 NameError"）。

    ## 为什么需要这条（实测的真缺陷）

    `infra/llm/client.py:662` 的 `_record_failure` 签名写着：

        provider: ProviderCapability,

    而全仓**从未定义过 `ProviderCapability`** —— 正确的类型是 `ProviderSpec`。
    这个错误**可以在仓库里存活任意久而不被发现**，原因是一个巧合：

        from __future__ import annotations

    让**所有注解都变成惰性字符串**（PEP 563），于是：

        · import 该模块 → 通过（注解不求值）
        · 调用该函数   → 通过（注解不参与运行）
        · 静态检查器   → 只有 `get_type_hints()` / mypy 才看

    本项目**没有 mypy**，所以这个拼写错误一直没被任何测试发现。
    它属于"**只写不读**"的变体：写了注解，但没有任何机制读它。

    ## 判定口径

    收集每个文件可见的名字（import / 定义 / 赋值 / 参数）+ `builtins`，
    然后检查**函数签名与 AnnAssign 的注解**里的裸 `Name` 是否都在其中。

    ⚠️ 只看**裸名**（`ast.Name`），不管 `x.y` 属性访问（那属于运行时属性，
    AST 层面无法判定，会误报）。
    """
    import builtins  # noqa: PLC0415

    known_builtins = set(dir(builtins))
    offenders: list[str] = []

    for path in _iter_py(src_root):
        tree = _parse(path)
        visible: set[str] = set(known_builtins)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    visible.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    visible.add(a.asname or a.name)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visible.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                visible.add(node.id)
            elif isinstance(node, ast.arg):
                visible.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                visible.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                visible.update(node.names)

        checked: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                checked.append(node.annotation)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = (list(node.args.args) + list(node.args.kwonlyargs)
                        + list(node.args.posonlyargs))
                for a in args:
                    if a.annotation:
                        checked.append(a.annotation)
                if node.returns:
                    checked.append(node.returns)

        for ann in checked:
            for sub in ast.walk(ann):
                if isinstance(sub, ast.Name) and sub.id not in visible:
                    offenders.append(
                        f"{path.relative_to(src_root)}:{sub.lineno} "
                        f"注解名 `{sub.id}` 未定义"
                    )

    assert not offenders, (
        "注解引用了不存在的名字（`from __future__ import annotations` "
        "会让它静默存活）：\n  " + "\n  ".join(sorted(set(offenders)))
    )


def test_rule22_function_bodies_reference_defined_names(src_root: Path) -> None:
    """规则 22：**函数体内**引用的名字也必须在作用域链上可见。

    ## 为什么需要这条（规则 21 的相邻缺口，实测踩到）

    规则 21 只看**注解** —— 而 `from __future__ import annotations` 让注解
    惰性求值，那是它存在的理由。但**函数体**里的名字是**立即求值**的：
    写错就 `NameError`。

    实测（阶段 2-3 收尾）：`commands/learning.py` 的 `_gen_with_llm` 里
    用了 `get_settings()` 却**没 import** —— 规则 21 抓不到
    （它不看函数体），单测也抓不到（该函数**需要 API key 才会被调用**，
    测试里不会走进去）。最终是**手工跑真实 CLI** 才暴露：

        ⚠ LLM 生成失败（NameError: name 'get_settings' is not defined）

    ★ 这与规则 21 修的 `ProviderCapability` 是**同一类缺陷**
    （"引用了不存在的名字"），只是位置从注解换到了函数体。
    两处都有测试覆盖才不会漏。

    ## ★★ 为什么改用 stdlib `symtable`（初版手写 AST 分析是**无效契约**）

    初版手写 AST 作用域分析，有**两个互相掩盖**的 bug，实测（变异测试发现）：

      · `_collect_bound(tree, ...)` 对**整棵树** `ast.walk` → 把"函数体内的
        import"也当成**模块级**绑定 → **函数 A 里 import 的名字对函数 B 可见**
      · `_check_scope` 用 `ast.walk(scope)` → 把**嵌套函数体内的 Name**
        拿外层可见集去判

    **第二个 bug 恰好被第一个掩盖**：泄漏把名字塞进了模块级可见集，
    于是嵌套函数体里的引用"看起来"是可见的，不报错。
    两者相抵 → 删掉 `_gen_with_llm` **自己**的 import 时规则**照样通过**
    （当时文件里 `_has_api_key` 也 import 了同名符号，双重掩盖）。

    ⇒ **一个"抓不到问题"的契约测试等于没有。** 这正是本项目已记录的教训。

    ★ 手写作用域分析要同时处理 6 类边界，而本项目里这些构造**都真实存在**
    （实测：推导式 247 处 · 海象 10 处 · global 5 处 · 类体绑定 182 处 ·
    lambda 16 处）。任何一处判错 = 误报；而**误报会诱使人删规则**
    （规则 12 第一次运行即误报过，故特意用 AST 而非子串匹配）。

    `symtable` 是 **CPython 自己**判定 local/free/global 用的模块 ——
    不再有"我猜错了"的余地。36 条构造样本电池逐条钉住语义
    （见 `scripts/_probe_rule22_final.py`）。

    ## 判定口径

    一个 `Load` 名若既非 local / free / nonlocal，又不在**模块级**绑定里、
    也不是内置名 → 运行时必然 `NameError`。

    ★ **模块级绑定只取 `symtable` 顶层表** —— 这正是不再泄漏的关键。
    唯一例外：`global g; g = 1` 是**显式声明**的模块级绑定，须递归收集
    （可深藏在嵌套函数里）。

    ★ AST↔symtable 的唯一差异：**PEP 709**（Python 3.12+）把推导式**内联**了，
    `symtable` 不再为它建子表（实测 39/79 个文件的推导式对不上）。
    ⇒ AST 侧也**不把推导式当作用域**，两边即完全对齐；
    推导式目标名由 `symtable` 记成外层函数的 local，天然放行。

    不检查的（会误报，故明确排除）：
      · **注解**（`x: T` / `def f(a: T) -> T`）—— 归规则 21，本规则不重复报
      · 属性访问（`x.y` 里的 `y` 是运行时属性，AST 判不了）
    """
    offenders: list[str] = []

    for path in _iter_py(src_root):
        source = path.read_text(encoding="utf-8")
        if not source.strip():
            continue
        found = _undefined_names_in_bodies(source, str(path))
        rel = path.relative_to(src_root)
        offenders.extend(
            f"{rel}:{lineno} 作用域 {scope} 内引用了未定义名 `{name}`"
            for lineno, scope, name in found
        )

    assert not offenders, (
        "函数体引用了未定义的名字（**运行到那一行才炸**，"
        "且只在该分支被走到时才暴露）：\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


# --------------------------------------------------------------------------- #
# 规则 22 的判定实现（`symtable` 定可见性 + AST 取行号）
#
# ★ 抽成模块级函数而非测试内闭包：便于 `scripts/_probe_rule22_final.py`
#   用同一份实现跑构造样本电池，避免"探针里对、接回来错"。
# --------------------------------------------------------------------------- #

#: `symtable` 不给行号，故用 `(作用域名, 定义行)` 作 AST↔symtable 的对接 key
_BUILTIN_NAMES = set(dir(builtins))
_ALWAYS_VISIBLE = {"__file__", "__name__", "__doc__", "__package__", "self", "cls"}


def _module_level_bindings(top: symtable.SymbolTable) -> set[str]:
    """模块级绑定 —— **只取顶层表**。

    ★ 这是本规则的关键。初版对整棵 AST `ast.walk`，把函数体内的 import
    也算成模块级绑定，于是"函数 A 里 import 的名字"对函数 B 可见（漏报）。
    """
    bound = {
        s.get_name()
        for s in top.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace()
    }

    def _collect_declared_globals(table: symtable.SymbolTable) -> None:
        """`global g; g = 1` 是**显式**的模块级绑定，须递归收（可深藏嵌套）。"""
        bound.update(
            s.get_name() for s in table.get_symbols()
            if s.is_global() and s.is_assigned()
        )
        for child in table.get_children():
            _collect_declared_globals(child)

    _collect_declared_globals(top)
    return bound


def _undefined_by_scope(source: str, filename: str) -> dict[tuple[str, int], set[str]]:
    """`(作用域名, 定义行)` → 该作用域内"被引用但作用域链上无绑定"的名字。"""
    top = symtable.symtable(source, filename, "exec")
    module_bound = _module_level_bindings(top)
    out: dict[tuple[str, int], set[str]] = {}

    def _walk(table: symtable.SymbolTable) -> None:
        # 顶层表的 get_name() 是 "top"、行号 0；AST 侧统一记作 "<module>"/0
        key = ("<module>", 0) if table.get_type() == "module" else (
            table.get_name(), table.get_lineno())
        names: set[str] = set()
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced():
                continue
            # local / free / nonlocal 都是"作用域链上已绑定" → 放行
            if sym.is_local() or sym.is_free() or sym.is_nonlocal():
                continue
            if name in module_bound or name in _BUILTIN_NAMES or name in _ALWAYS_VISIBLE:
                continue
            names.add(name)
        out[key] = names
        for child in table.get_children():
            _walk(child)

    _walk(top)
    return out


def _scope_key_of(node: ast.AST) -> tuple[str, int]:
    """AST 作用域节点 → 与 `symtable` 一致的 `(名, 行)` key。

    ★ 刻意**不含推导式**：PEP 709（Python 3.12+）已把推导式内联，
    `symtable` 不为它建子表。若这里认它、那边不认，就会两边对不上。
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name, node.lineno)
    if isinstance(node, ast.Lambda):
        return ("lambda", node.lineno)
    return ("<module>", 0)


def _undefined_names_in_bodies(source: str, filename: str) -> list[tuple[int, str, str]]:
    """→ `[(行号, 作用域名, 名字)]`，跳过注解（那是规则 21 的职责）。"""
    undefined = _undefined_by_scope(source, filename)
    tree = ast.parse(source, filename=filename)
    out: list[tuple[int, str, str]] = []

    def _visit_args(args: ast.arguments, key: tuple[str, int]) -> None:
        """参数**注解**归注解；**默认值**在外层作用域求值。"""
        for arg in [*args.posonlyargs, *args.args, args.vararg,
                    *args.kwonlyargs, args.kwarg]:
            if arg is not None and arg.annotation is not None:
                _visit(arg.annotation, key, in_annotation=True)
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                _visit(default, key)

    def _visit(node: ast.AST, key: tuple[str, int], in_annotation: bool = False) -> None:
        if isinstance(node, ast.Name):
            if (isinstance(node.ctx, ast.Load) and not in_annotation
                    and node.id in undefined.get(key, ())):
                out.append((node.lineno, key[0], node.id))
            return

        # 函数/类：装饰器、默认值、基类在**外层**求值；只有 body 是新作用域
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                _visit(dec, key)
            _visit_args(node.args, key)
            if node.returns is not None:
                _visit(node.returns, key, in_annotation=True)
            for type_param in getattr(node, "type_params", []):
                _visit(type_param, key)
            for stmt in node.body:
                _visit(stmt, _scope_key_of(node))
            return

        if isinstance(node, ast.Lambda):
            _visit_args(node.args, key)
            _visit(node.body, _scope_key_of(node))
            return

        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                _visit(dec, key)
            for base in node.bases:
                _visit(base, key)
            for kw in node.keywords:
                _visit(kw, key)
            for type_param in getattr(node, "type_params", []):
                _visit(type_param, key)
            for stmt in node.body:
                _visit(stmt, _scope_key_of(node))
            return

        if isinstance(node, ast.AnnAssign):
            _visit(node.target, key)
            if node.annotation is not None:
                _visit(node.annotation, key, in_annotation=True)
            if node.value is not None:
                _visit(node.value, key)
            return

        if isinstance(node, ast.arg):
            if node.annotation is not None:
                _visit(node.annotation, key, in_annotation=True)
            return

        for child in ast.iter_child_nodes(node):
            _visit(child, key, in_annotation)

    _visit(tree, ("<module>", 0))
    return out


#: 规则 22 的构造样本电池：`(标题, 源码, 期望报出的名字集合)`。
#:
#: ★ 为什么把它放进契约测试（而不只是探针脚本）：
#: 一条**判定逻辑复杂**的规则，它自己就是被测试对象。
#: 若只靠"跑一遍现有 src 没报错"来确认，那么规则**退化成永远放行**
#: 也照样绿灯 —— 本项目实测**已经发生过一次**（旧实现的两个 bug 互相掩盖，
#: 删掉真依赖仍通过）。所以两个方向都要钉住：
#:   · 必须报：真 NameError 一律抓出（防漏报）
#:   · 必须放行：合法的闭包/推导式/海象/global 一律不报（防误报）
#: 误报同样致命 —— 它会诱使人删掉规则而不是修代码。
_RULE22_CASES: list[tuple[str, str, set[str]]] = [
    # ── 必须报（防漏报） ────────────────────────────────────────────
    ("原缺陷：函数体用了没 import 的名字",
     "def a():\n    return get_settings()\n", {"get_settings"}),
    ("★ 函数 A 里 import，函数 B 里用（旧实现漏报）",
     "def a():\n    import re\n    return re\ndef b():\n    return re.compile('x')\n",
     {"re"}),
    ("★ 类体名被方法裸用（Python 不做类作用域闭包）",
     "class C:\n    y = 1\n    def m(self):\n        return y\n", {"y"}),
    ("嵌套函数用未定义名",
     "def a():\n    def b():\n        return ZZZ\n    return b\n", {"ZZZ"}),
    ("推导式里用未定义名",
     "def f(ys):\n    return [x + ZZZ for x in ys]\n", {"ZZZ"}),
    ("装饰器用未定义名",
     "@deco\ndef f():\n    return 1\n", {"deco"}),
    ("默认值用未定义名",
     "def f(a=DDD):\n    return a\n", {"DDD"}),
    ("类基类用未定义名",
     "class C(Base):\n    pass\n", {"Base"}),
    ("lambda 体用未定义名",
     "f = lambda x: x + QQQ\n", {"QQQ"}),
    ("模块级用未定义名",
     "X = UNDEF_AT_MODULE\n", {"UNDEF_AT_MODULE"}),
    ("三层嵌套函数用未定义名",
     "def a():\n    def b():\n        def c():\n            return DEEP\n"
     "        return c\n    return b\n", {"DEEP"}),
    ("global 只读但模块级无定义",
     "def f():\n    global NOPE\n    return NOPE\n", {"NOPE"}),
    ("async 函数体用未定义名",
     "async def f():\n    return AWAITED\n", {"AWAITED"}),
    # ── 必须放行（防误报） ──────────────────────────────────────────
    ("模块级 import 跨函数可见",
     "import re\ndef a():\n    return re\ndef b():\n    return re.compile('x')\n", set()),
    ("函数内 import 在**本函数**内可用",
     "def a():\n    import re\n    return re.compile('x')\n", set()),
    ("闭包引用外层局部",
     "def outer():\n    d = 1\n    def inner():\n        return d\n    return inner\n", set()),
    ("嵌套函数引用外层参数",
     "def f(p):\n    def g():\n        return p\n    return g\n", set()),
    ("推导式目标（PEP 709 内联）",
     "def f(ys):\n    return [x * 2 for x in ys]\n", set()),
    ("海象在推导式内",
     "def f(ys):\n    return [y for x in ys if (y := x) > 0]\n", set()),
    ("with ... as",
     "def f(p):\n    with open(p) as fh:\n        return fh.read()\n", set()),
    ("for 解包目标",
     "def f(d):\n    for k, v in d.items():\n        print(k, v)\n", set()),
    ("except ... as",
     "def f():\n    try:\n        pass\n    except ValueError as e:\n        return e\n", set()),
    ("lambda 参数",
     "def f():\n    return lambda x: x + 1\n", set()),
    ("global 声明后赋值，另处可读",
     "def f():\n    global G\n    G = 1\ndef g():\n    return G\n", set()),
    ("global 深藏嵌套函数",
     "def a():\n    def b():\n        global G2\n        G2 = 1\ndef c():\n    return G2\n", set()),
    ("类名在模块级可见",
     "class C:\n    pass\ndef f():\n    return C()\n", set()),
    ("类体自引用（保守放行）",
     "class C:\n    x = 1\n    z = x + 1\n", set()),
    ("方法引用模块级名",
     "HELPER = 1\nclass C:\n    def m(self):\n        return HELPER\n", set()),
    ("方法引用模块级 import",
     "import os\nclass C:\n    def m(self):\n        return os.sep\n", set()),
    ("内置名",
     "def f(xs):\n    return len(list(xs))\n", set()),
    ("match 绑定（名字挂在 MatchAs 上，不是 Name 节点）",
     "def f(v):\n    match v:\n        case {'a': a}:\n            return a\n    return None\n", set()),
    ("类型注解引用未定义名 → 归规则 21，本规则不重复报",
     "def f(a: UndefType) -> UndefRet:\n    return a\n", set()),
    ("变量注解引用未定义名 → 归规则 21",
     "x: UndefType = 1\n", set()),
    ("async for / async with",
     "async def f(aiter, cm):\n    async with cm() as h:\n        async for i in aiter():\n"
     "            print(h, i)\n", set()),
    ("嵌套类的方法",
     "class A:\n    class B:\n        def m(self):\n            return A()\n", set()),
    ("self 与 cls",
     "class C:\n    @classmethod\n    def m(cls):\n        return cls\n"
     "    def n(self):\n        return self\n", set()),
]


@pytest.mark.parametrize(
    ("title", "source", "expected"),
    _RULE22_CASES,
    ids=[c[0] for c in _RULE22_CASES],
)
def test_rule22_judgement_matches_python_semantics(
    title: str, source: str, expected: set[str],
) -> None:
    """规则 22 的**自检**：判定必须与 CPython 真实语义一致。

    ★ 北向（漏报）与南向（误报）都钉住 —— 详见 `_RULE22_CASES` 的说明。
    """
    got = {name for _, _, name in _undefined_names_in_bodies(source, f"<case:{title}>")}
    assert got == expected, (
        f"规则 22 判定与 Python 语义不符：{title}\n"
        f"  期望报出 {sorted(expected) or '（无）'}\n"
        f"  实际报出 {sorted(got) or '（无）'}"
    )
