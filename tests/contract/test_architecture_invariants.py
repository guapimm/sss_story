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


MODE_BRANCH_TOKENS = {"book_kind", "is_story", "_is_story", "book_kind_"}


def test_rule03_no_mode_branching_in_domain_and_nodes(src_root: Path) -> None:
    """规则 3：`domain/` 与 `graph/nodes/` 不得直接判模式。

    防的是旧项目「门面建成但门面外仍有 30+ 处直读 `book_kind`」。
    所有模式差异必须经 `modes/` 层的 Protocol（决策 D-08）。
    """
    offenders: list[str] = []
    for sub in ("domain", "graph/nodes"):
        for path in _iter_py(src_root / sub):
            tree = _parse(path)
            names = _imported_names(tree)
            if "storyforge.modes" in names or "modes" in names:
                continue  # 模式层本身允许
            hits = sorted(
                {n for _, n in _attr_names(tree) if n in MODE_BRANCH_TOKENS}
                | {s for _, s in _string_literals(tree) if s in MODE_BRANCH_TOKENS}
            )
            if hits:
                offenders.append(f"{path.relative_to(src_root)}: {hits}")
    assert not offenders, (
        "共享代码不得判模式（应经 modes/ Protocol）：\n  " + "\n  ".join(offenders)
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
    """
    allowed_files = {"capability.py"}  # 只允许在能力层解析 API key 环境变量
    offenders: list[str] = []
    for path in _iter_py(src_root):
        if path.name in allowed_files or path.name == "config.py":
            continue
        if "os.environ" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(src_root)))
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
    """规则 16：LLM 节点必须有幂等键。

    防的是 `langgraph#7417`（53 条评论仍 open）：**>180s 的调用会被静默重放**
    → 2~3× 冗余成本。而旧项目实测 `editor` 平均 **117s**、`architect` **72s**，
    章节生成极易超阈值。
    """
    nodes_dir = src_root / "graph" / "nodes"
    if not nodes_dir.exists() or not list(_iter_py(nodes_dir)):
        pytest.skip("尚无节点实现")
    offenders: list[str] = []
    for path in _iter_py(nodes_dir):
        text = path.read_text(encoding="utf-8")
        if "def " not in text:
            continue
        if "idempotency_key" not in text and "idem" not in text:
            offenders.append(str(path.relative_to(src_root)))
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
