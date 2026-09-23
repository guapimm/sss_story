"""契约：结构化输出的节点 prompt 必须带**字段名骨架**。

## 为什么这是一条契约（实测驱动）

真实运行（小米 MiMo）反复出现同一个失败：**字段名漂移**。

    schema 要          模型给
    volume_no      →  volume_number / volume_number
    title          →  volume_title
    core_conflict  →  core_focus
    chapter_no     →  "第6章：潜入上海…"

每一次不匹配都会让**整轮规划失败**（不是"抽得差"，是全挂）。
我一开始的应对是往别名表里加映射 —— 但那是**打地鼠**：
模型每次可以造一个新名字，而别名表永远追不上。

根因不是模型不听话，而是**prompt 只用散文描述字段，没给出确切骨架**。
散文说「每卷给出卷号、标题、核心矛盾」，模型自然选它最顺的英文名。

因此契约是：**任何 `complete_structured` 的节点，其 system prompt
必须包含一段 JSON 骨架，把字段名逐字写出来。**

这条契约同时约束了另外两件事：
  · 骨架里的字段名必须真的存在于对应 schema（防骨架本身写错）
  · 骨架必须覆盖 schema 的**全部必填字段**（防漏写导致模型漏给）
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "storyforge"

#: 需要「prompt 带骨架」的节点 → (模块, prompt 常量名, schema 类名)
#:
#: ★★ 2026-09-18 扩容（阶段 1 收尾实测）：
#: 原来只登记 **4 个规划节点**，**漏了 3 个抽取节点** ——
#: 于是它们长期没有 JSON 骨架，而这个契约测试**对此全绿**。
#:
#: 漏检的后果（真实 `write` 崩溃）：
#:
#:     StructuredOutputError: 节点 scene_extractor 结构化输出校验失败
#:     - `state_updates.0`：Input should be an object
#:
#: 更危险的一例（**静默**）：
#:
#:     "state_updates": {"location": "+楼梯间", "emotion": "恐慌→恐惧"}
#:
#: 没有骨架 ⇒ 模型照 prompt 里的散文描述（"位置/境界/目标/情绪/装备等"）
#: 自造外层键 ⇒ 系统把 `location` 当成**角色名** ⇒ 4 条状态全挂在
#: 不存在的实体上（那一章主角是"林默"）→ 数据入库、看起来正常、实际全错。
#:
#: ★ 这个漏洞本身就是本契约的教训：**"抓不到问题"的契约等于没有**
#: （与 `_PROMPT_REQUIRED_OPTIONAL` 那条注释同一个道理）。
STRUCTURED_NODES: tuple[tuple[str, str, str, str], ...] = (
    ("bible_architect", "BIBLE_ARCHITECT_SYSTEM", "BibleOut", "graph/nodes/bible_architect.py"),
    ("volume_planner", "VOLUME_PLANNER_SYSTEM", "SeriesPlanOut", "graph/nodes/volume_planner.py"),
    ("skeleton_planner", "SKELETON_PLANNER_SYSTEM", "SkeletonOut", "graph/nodes/skeleton_planner.py"),
    ("detail_planner", "DETAIL_PLANNER_SYSTEM", "DetailBatchOut", "graph/nodes/detail_planner.py"),
    # ── 抽取三节点（阶段 1 收尾补入）──
    ("scene_extractor", "SCENE_EXTRACTOR_SYSTEM", "SceneExtractionOut",
     "graph/nodes/extractors.py"),
    ("entity_resolver", "ENTITY_RESOLVER_SYSTEM", "EntityResolutionOut",
     "graph/nodes/extractors.py"),
    ("relation_extractor", "RELATION_EXTRACTOR_SYSTEM", "RelationExtractionOut",
     "graph/nodes/extractors.py"),
    # ── learn 规划三节点（阶段 2-3）──
    # ★ 这次是**主动登记**的：缺陷 11 的根因正是"契约漏登记抽取节点"
    # （设计完 prompt 却没进 STRUCTURED_NODES → 长期无骨架而契约全绿）。
    # 新增任何带 JSON 输出的节点都必须同时登记到这里。
    ("learning_map", "LEARNING_MAP_SYSTEM", "LearningMapOut",
     "infra/llm/learning_schemas.py"),
    ("learning_gap_check", "GAP_CHECK_SYSTEM", "GapCheckOut",
     "infra/llm/learning_schemas.py"),
    ("term_mapping", "MAPPING_SYSTEM", "TermMappingBatchOut",
     "infra/llm/learning_schemas.py"),
)

#: 骨架里必须出现的字段（schema 必填字段）——由测试动态补全，
#: 这里只登记"实例对象里不出现、但模型必须知道"的嵌套字段。
_NESTED_REQUIRED: dict[str, tuple[str, ...]] = {
    "SeriesPlanOut": ("volume_no", "chapter_no"),
    "DetailBatchOut": ("scenes", "goal", "obstacle", "outcome", "why_now"),
    "SkeletonOut": ("arcs", "kind", "segments", "characters", "factions"),
    # 抽取节点的嵌套必填字段（形状漂移的高发区）
    "SceneExtractionOut": ("description",),
    "EntityResolutionOut": ("mentions",),
    "RelationExtractionOut": ("from_entity", "to_entity", "kind"),
}

#: ★ **有默认值、但校验器会强制要求**的顶层字段 → 也必须写进 prompt 骨架。
#:
#: ## 为什么需要这份登记（实测发现的契约漏洞）
#:
#: `test_skeleton_covers_all_required_top_level_fields` 只查 `is_required()`，
#: 而带 `default_factory` 的字段不算必填 → **契约漏掉它们**。
#:
#: 后果实测：我把 `places` 从 prompt 骨架里删掉，**契约测试全绿** ——
#: 但 `validate_skeleton` 会因为 `new_locations` 未落实而判失败，
#: 而模型根本不知道要输出这个字段（prompt 没写）→ 必然整卷失败。
#:
#: 一个"抓不到问题"的契约测试等于没有（我主动做了这次证伪才发现的）。
#: 登记原则：**校验器会拿它判成败** ⇒ 必须进 prompt，哪怕它有默认值。
_PROMPT_REQUIRED_OPTIONAL: dict[str, tuple[str, ...]] = {
    "SkeletonOut": ("places",),
}


def _read(rel: str) -> str:
    return (SRC_ROOT / rel).read_text(encoding="utf-8")


#: schema 定义**按职责分布在三个模块**（不是一个门面）：
#:
#:     schemas.py              规划期（圣经/台阶/骨架/细纲/审校）
#:     extraction_schemas.py   抽取期（正文 → 结构化事实）
#:     learning_schemas.py     learn 规划期（学习地图/平替修辞）
#:
#: ★ 为什么契约要**跨模块解析**而不是要求"全都在 schemas.py"：
#: `schemas.py` 已 791/800 行，物理上放不下 —— 而契约规则 10
#: 是**为旧项目 `pipeline.py` 8,607 行立的**，撞限时该拆文件、不该放宽。
#: 三个模块的拆分依据都是**职责**（已在本文件与各模块 docstring 记录）。
#:
#: ⚠️ 早期版本用 `getattr(schemas, name)` 单点解析 —— 那会让
#: "把 schema 挪到职责更合适的模块"这件事**被契约挡住**，
#: 变相鼓励往 `schemas.py` 里硬塞。
_SCHEMA_MODULES: tuple[str, ...] = (
    "storyforge.infra.llm.schemas",
    "storyforge.infra.llm.extraction_schemas",
    "storyforge.infra.llm.learning_schemas",
)


def _resolve_schema(schema_name: str):
    """在三个 schema 模块里找该类；找不到则**明确报错**（不静默）。"""
    import importlib

    for mod_name in _SCHEMA_MODULES:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, schema_name):
            return getattr(mod, schema_name)
    raise AssertionError(
        f"在 {list(_SCHEMA_MODULES)} 里都找不到 schema {schema_name!r} —— "
        f"登记的名字可能拼错了（那会让这条契约形同虚设）"
    )


def _prompt_constant(rel: str, const: str) -> str:
    tree = ast.parse(_read(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == const:
                    val = node.value
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        return val.value
    raise AssertionError(f"{rel} 里找不到字符串常量 {const}")


@pytest.mark.parametrize("node,const,schema_name,rel", STRUCTURED_NODES)
def test_prompt_contains_json_skeleton(node, const, schema_name, rel) -> None:
    """★ prompt 必须含 `{` 开头、含字段名的 JSON 骨架。"""
    prompt = _prompt_constant(rel, const)
    blocks = re.findall(r"```json\s*(.+?)```", prompt, re.S)
    assert blocks, (
        f"{node} 的 prompt 没有 JSON 骨架 —— 只用散文描述字段会让模型自造同义名"
        f"（实测：volume_number / volume_title / core_focus），"
        f"而每个名字对不上都会让整轮失败。请加一段 ```json 骨架。"
    )


@pytest.mark.parametrize("node,const,schema_name,rel", STRUCTURED_NODES)
def test_skeleton_names_match_schema_fields(node, const, schema_name, rel) -> None:
    """★ 骨架里的字段名必须真的在 schema 里存在。

    防的是"骨架自己写错了" —— 那比没有骨架更糟：模型会照着一个
    不存在的字段名输出，然后必然校验失败。
    """
    schema = _resolve_schema(schema_name)
    valid = set(schema.model_fields)
    # 嵌套模型的字段也要算合法
    for f in schema.model_fields.values():
        ann = f.annotation
        for sub in _model_types(ann):
            valid |= set(sub.model_fields)

    prompt = _prompt_constant(rel, const)
    blocks = re.findall(r"```json\s*(.+?)```", prompt, re.S)
    blob = "\n".join(blocks)

    # 提取骨架里的键名（"key": 形式）
    keys = set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', blob))
    unknown = keys - valid
    assert not unknown, (
        f"{node} 的骨架里有 schema 中不存在的字段：{sorted(unknown)}。"
        f"模型会照着不存在的字段名输出，必然失败。"
    )


@pytest.mark.parametrize("node,const,schema_name,rel", STRUCTURED_NODES)
def test_skeleton_covers_all_required_top_level_fields(node, const, schema_name, rel) -> None:
    """★ 骨架必须覆盖 schema 的**全部必填**顶层字段。

    漏写的字段模型很可能就不给 —— 而它们是必填，缺一个就整轮失败。
    """
    schema = _resolve_schema(schema_name)
    required = {
        name for name, f in schema.model_fields.items() if f.is_required()
    }
    prompt = _prompt_constant(rel, const)
    blob = "\n".join(re.findall(r"```json\s*(.+?)```", prompt, re.S))
    keys = set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', blob))

    missing = required - keys
    assert not missing, (
        f"{node} 的骨架漏了必填字段 {sorted(missing)} —— "
        f"模型很可能就不给它们，而它们是必填"
    )


@pytest.mark.parametrize("node,const,schema_name,rel", STRUCTURED_NODES)
def test_skeleton_covers_validator_enforced_optional_fields(
    node, const, schema_name, rel
) -> None:
    """★ **校验器会据以判成败**的字段必须在骨架里 —— 哪怕它有默认值。

    ## 为什么单独一条（实测发现的漏洞）

    上一条只查 `is_required()`。而 `SkeletonOut.places` 带 `default_factory`，
    不算必填 → **漏检**。实测把 `places` 从 prompt 骨架删掉后契约**全绿**，
    但 `validate_skeleton` 会因"新地图未落实"判失败，而模型根本不知道
    要输出该字段（prompt 没写）→ 必然整卷失败。

    登记原则：**校验器会拿它判成败 ⇒ 必须进 prompt**。
    """
    want = _PROMPT_REQUIRED_OPTIONAL.get(schema_name, ())
    if not want:
        pytest.skip(f"{schema_name} 未登记'校验器强制的可选字段'")
    schema = _resolve_schema(schema_name)
    # 登记的名字必须真在 schema 里（防登记笔误让本条形同虚设）
    unknown = [w for w in want if w not in schema.model_fields]
    assert not unknown, f"登记了 schema 里不存在的字段：{unknown}"

    blob = "\n".join(re.findall(r"```json\s*(.+?)```", _prompt_constant(rel, const), re.S))
    missing = [w for w in want if f'"{w}"' not in blob]
    assert not missing, (
        f"{node} 的 prompt 骨架漏了 {sorted(missing)} —— 校验器会拿它们判成败，"
        f"但模型不知道要输出（prompt 没写）→ 必然失败。"
        f"（这些字段有默认值，所以不会被'必填字段'那条测试抓到）"
    )


@pytest.mark.parametrize("node,const,schema_name,rel", STRUCTURED_NODES)
def test_skeleton_covers_nested_required_fields(node, const, schema_name, rel) -> None:
    """嵌套对象的字段名也要写出（如 milestone 的 `chapter_no`）。"""
    want = _NESTED_REQUIRED.get(schema_name, ())
    if not want:
        pytest.skip("该 schema 无登记的嵌套必填字段")
    prompt = _prompt_constant(rel, const)
    blob = "\n".join(re.findall(r"```json\s*(.+?)```", prompt, re.S))
    missing = [w for w in want if f'"{w}"' not in blob]
    assert not missing, (
        f"{node} 的骨架漏了嵌套字段 {missing} —— "
        f"实测模型会因此把里程碑写成裸章号或自造名"
    )


def _model_types(annotation) -> list:
    """递归挖出注解里所有 BaseModel 子类（支持 `list[X]` / `X | None` / 嵌套）。

    ⚠️ 必须**递归**：`SeriesPlanOut.volumes` 是 `list[VolumePlanOut]`，
    而 `VolumePlanOut.milestones` 是 `list[MilestoneOut]` ——
    只看一层就找不到 `MilestoneOut`，于是骨架里的 `chapter_no`
    会被误判为"schema 中不存在的字段"（实测踩到）。
    """
    from pydantic import BaseModel

    out: list = []
    seen: set = set()

    def walk(ann) -> None:
        args = getattr(ann, "__args__", ())
        for a in args or ():
            if inspect.isclass(a) and issubclass(a, BaseModel):
                if a not in seen:
                    seen.add(a)
                    out.append(a)
                    for f in a.model_fields.values():
                        walk(f.annotation)
            else:
                walk(a)
        if inspect.isclass(ann) and issubclass(ann, BaseModel):
            if ann not in seen:
                seen.add(ann)
                out.append(ann)
                for f in ann.model_fields.values():
                    walk(f.annotation)

    walk(annotation)
    return out


# --------------------------------------------------------------------------- #
# ★★ 文件型 prompt 的骨架契约（2026-09-23 新增）
# --------------------------------------------------------------------------- #
#: 以**独立 .md 文件**存放的 prompt → (文件名, schema 类名)
#:
#: ## 为什么必须补这一条（缺陷 11 的漏洞再犯）
#:
#: 上面的 `STRUCTURED_NODES` 只覆盖**写在 .py 里的字符串常量**
#: （`ast` 解析 `Assign` 找 `ast.Constant`）。而 `style_critic` 的 prompt
#: 是 `infra/prompts/style_critic.md`，由 `library._load()` 读文件 ——
#: **它不在任何一条契约的保护范围内**。
#:
#: 后果实测（2026-09-23 切 `mimo-v2.6-flash` 后真实 `write` 崩）：
#:
#:     StructuredOutputError: 节点 style_critic 结构化输出校验失败（3 次尝试）
#:       - 缺必填字段：issues.0.description  （×10 条）
#:     原始输出：{"score":6.5,"issues":[{"type":"AI味","location":"…",
#:               "detail":"…","fix":"…"}, ...]}
#:
#: 模型给 `detail`/`fix`，schema 要 `description`/`suggestion` ——
#: **信息全都在，只是字段名不同**。而该 prompt 当时只写了"只输出 JSON"，
#: **一个字段名都没给**。
#:
#: ★ 这与缺陷 11 是**同一个根因的第二处**：契约漏登记 ⇒ 长期无骨架而全绿。
#:   ⇒ 登记原则：**凡是有 JSON 输出的节点，无论 prompt 放在哪，都必须登记**。
FILE_PROMPTS: tuple[tuple[str, str], ...] = (
    ("style_critic", "ReviewResultOut"),
)


@pytest.mark.parametrize("prompt_name,schema_name", FILE_PROMPTS)
def test_file_prompt_contains_json_skeleton(prompt_name, schema_name) -> None:
    """★ 文件型 prompt 必须含 ```json 骨架，且字段名与 schema 一致。"""
    path = SRC_ROOT / "infra" / "prompts" / f"{prompt_name}.md"
    assert path.exists(), f"登记的文件型 prompt 不存在：{path}"
    text = path.read_text(encoding="utf-8")

    blocks = re.findall(r"```json\s*(.+?)```", text, re.S)
    assert blocks, (
        f"{prompt_name}.md 没有 JSON 骨架 —— 只写「只输出 JSON」不够："
        f"模型会自造同义字段名（实测：detail / fix），"
        f"每个名字对不上都让整轮校验失败并**中断 write**。"
    )

    blob = "\n".join(blocks)

    # ① 骨架里的字段名必须真的在 schema 里
    schema = _resolve_schema(schema_name)
    valid = set(schema.model_fields)
    for f in schema.model_fields.values():
        for sub in _model_types(f.annotation):
            valid |= set(sub.model_fields)

    keys = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', blob))
    unknown = sorted(keys - valid)
    assert not unknown, (
        f"{prompt_name}.md 的骨架含 schema 中不存在的字段 {unknown} —— "
        f"模型会照着不存在的字段名输出，必然校验失败"
    )

    # ② schema 的**必填**字段必须都出现在骨架里
    required = {n for n, f in schema.model_fields.items() if f.is_required()}
    missing = sorted(required - keys)
    assert not missing, (
        f"{prompt_name}.md 的骨架漏了必填字段 {missing} —— "
        f"模型不知道要输出它 ⇒ 必然失败（这正是本次实测的崩溃）"
    )


def test_nested_schema_required_fields_appear_in_file_skeleton() -> None:
    """★ 嵌套模型的**必填**字段也必须出现在骨架里。

    `ReviewResultOut.issues` 是 `list[ReviewIssueOut]`，而
    `ReviewIssueOut.description` 是必填 —— 本次崩溃正是漏了它。
    只查顶层字段会漏掉这一层（`_NESTED_REQUIRED` 是手工登记，容易过期）。
    """
    schema = _resolve_schema("ReviewResultOut")
    nested_required: set[str] = set()
    for f in schema.model_fields.values():
        for sub in _model_types(f.annotation):
            nested_required |= {n for n, sf in sub.model_fields.items() if sf.is_required()}

    path = SRC_ROOT / "infra" / "prompts" / "style_critic.md"
    blob = "\n".join(re.findall(r"```json\s*(.+?)```", path.read_text(encoding="utf-8"), re.S))

    missing = sorted(w for w in nested_required if f'"{w}"' not in blob)
    assert not missing, (
        f"style_critic.md 的骨架漏了嵌套必填字段 {missing} —— "
        f"模型会自造同义名，导致 3 次重试全败"
    )

