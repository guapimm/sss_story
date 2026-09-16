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
STRUCTURED_NODES: tuple[tuple[str, str, str, str], ...] = (
    ("bible_architect", "BIBLE_ARCHITECT_SYSTEM", "BibleOut", "graph/nodes/bible_architect.py"),
    ("volume_planner", "VOLUME_PLANNER_SYSTEM", "SeriesPlanOut", "graph/nodes/volume_planner.py"),
    ("skeleton_planner", "SKELETON_PLANNER_SYSTEM", "SkeletonOut", "graph/nodes/skeleton_planner.py"),
    ("detail_planner", "DETAIL_PLANNER_SYSTEM", "DetailBatchOut", "graph/nodes/detail_planner.py"),
)

#: 骨架里必须出现的字段（schema 必填字段）——由测试动态补全，
#: 这里只登记"实例对象里不出现、但模型必须知道"的嵌套字段。
_NESTED_REQUIRED: dict[str, tuple[str, ...]] = {
    "SeriesPlanOut": ("volume_no", "chapter_no"),
    "DetailBatchOut": ("scenes", "goal", "obstacle", "outcome", "why_now"),
    "SkeletonOut": ("arcs", "kind", "segments", "characters", "factions"),
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
    import storyforge.infra.llm.schemas as S

    schema = getattr(S, schema_name)
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
    import storyforge.infra.llm.schemas as S

    schema = getattr(S, schema_name)
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
    import storyforge.infra.llm.schemas as S

    schema = getattr(S, schema_name)
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
