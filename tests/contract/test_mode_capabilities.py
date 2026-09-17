"""模式能力层的契约测试（决策 D-08）—— **双向矩阵**。

## 本文件防的是什么

旧项目 `flowmode/` 最重要的失败是**方向反了**：
story 侧给 learn 的钩子写了 **8 个 `@staticmethod` 空实现**
（`story_flow.py:171-201`）。后果：

    · 加 learn 时要**删掉** story 里的空实现（改共享代码，风险高）
    · 空实现**静默返回** `""` / `[]` → 调用方无法区分
      "learn 没做" 与 "learn 做了但结果为空"

本设计改为**正向着色**：learn 侧为自己的能力占位并 `raise`。

## 双向矩阵（本文件的核心）

| | story | learn |
|---|---|---|
| `prompts`/`genre`/`estimator` | ✅ 完整实现 | ⚠️ 占位（调用即抛） |
| `learning_map`/`ledger`/`cards`/`research`/`verifier` | ❌ **属性不存在** | ❌ 属性不存在（一期） |

"story 不实现 learn 专属能力"与"learn 不实现 story 的能力"
**两个方向都要断言** —— 只测一边会漏掉反向回归
（例如有人为了"让 learn 能跑"给 story 加 learn 的空实现，正是旧项目的形态）。
"""

from __future__ import annotations

import ast

import pytest

from storyforge.domain.enums import BookMode
from storyforge.modes import (
    LEARN_ONLY_CAPABILITIES,
    SHARED_CAPABILITIES,
    CapabilityNotAvailable,
    make_learn_mode,
    mode_for,
    require_capability,
)
from storyforge.modes import protocols as _protocols

pytestmark = pytest.mark.contract

#: 8 个 Protocol 的名字 → 它们对应的**能力属性名**（模式对象上的属性）
PROTOCOL_CAPABILITIES: dict[str, str] = {
    "LearningMapProvider": "learning_map",
    "TeachingLedgerProvider": "ledger",
    "KnowledgeCardProvider": "cards",
    "ResearchProvider": "research",
    "FactVerifier": "verifier",
    "Estimator": "estimator",
    "GenrePlugin": "genre",
    "PromptPack": "prompts",
}


# --------------------------------------------------------------------------- #
# 8 个 Protocol 的存在性与形状
# --------------------------------------------------------------------------- #


def test_eight_protocols_are_defined() -> None:
    """★ 文档要求的 8 个 Protocol 必须**真的存在**。

    实测：在阶段 1 之前，全仓 grep 这 8 个名字在 `*.py` 里**零命中** ——
    `doc/01-总体架构.md:369-398` 设计好了，但一行都没实现。
    """
    assert len(PROTOCOL_CAPABILITIES) == 8
    missing = [
        n for n in PROTOCOL_CAPABILITIES
        if not hasattr(_protocols, n)
    ]
    assert not missing, f"这些 Protocol 未定义：{missing}"


def test_protocols_are_runtime_checkable() -> None:
    """8 个 Protocol 都须 `@runtime_checkable` —— 否则无法用 `isinstance` 验证实现。"""
    not_rt = [
        n for n in PROTOCOL_CAPABILITIES
        if not getattr(getattr(_protocols, n), "_is_runtime_protocol", False)
    ]
    assert not not_rt, f"这些 Protocol 不是 runtime_checkable：{not_rt}"


def test_capability_names_match_protocol_set() -> None:
    """★ 能力名集合必须与 8 个 Protocol **精确对应**（防漏登记/多登记）。"""
    assert set(PROTOCOL_CAPABILITIES.values()) == (
        LEARN_ONLY_CAPABILITIES | SHARED_CAPABILITIES
    ), (
        f"能力名不一致：\n"
        f"  Protocol 推出：{sorted(PROTOCOL_CAPABILITIES.values())}\n"
        f"  LEARN_ONLY    ：{sorted(LEARN_ONLY_CAPABILITIES)}\n"
        f"  SHARED        ：{sorted(SHARED_CAPABILITIES)}"
    )
    assert not (LEARN_ONLY_CAPABILITIES & SHARED_CAPABILITIES), (
        "同一个能力不能既是 learn 专属又是共享"
    )


def test_story_implements_exactly_shared_capabilities() -> None:
    """★ 方向一：**story 只实现共享的 3 个**，不得为 learn 专属能力占位。

    这条正是旧项目失败的形状 —— 若这里失败，说明有人又在
    story 侧给 learn 写空实现了（D-08 明确禁止）。
    """
    story = mode_for(BookMode.STORY)
    for cap in SHARED_CAPABILITIES:
        assert hasattr(story, cap), f"story 缺少共享能力 {cap!r}"
    leaked = [c for c in LEARN_ONLY_CAPABILITIES if hasattr(story, c)]
    assert not leaked, (
        f"story 侧出现了 learn 专属能力的占位：{leaked}\n"
        f"这正是旧项目 `story_flow.py:171-201` 的 8 个空实现 —— "
        f"learn 的能力应由 learn 侧正向占位（modes/learn.py），"
        f"而不是让 story 提供空实现（空实现会静默返回错误结果）。"
    )


def test_learn_does_not_implement_story_side_either() -> None:
    """★ 方向二：**learn 也不得给 learn 专属能力放假属性**（一期）。

    一期 learn 的 5 个 Provider 是显式 `raise`，且**不挂属性** ——
    于是 `require_capability` 在**取的时候**就炸，
    而不是拿到对象后在调用时才炸（更早暴露）。

    ⚠️ 这条防的是"为了让 learn 看起来能跑"而补一层空壳代理。
    """
    learn = make_learn_mode()
    for cap in LEARN_ONLY_CAPABILITIES:
        assert not hasattr(learn, cap), (
            f"learn 侧出现了 {cap!r} 属性（一期应为**不存在**）—— "
            f"若已实现，请更新本测试与 LEARN_ONLY_CAPABILITIES 的语义"
        )
    # 但它必须有共享能力（否则 learn 连构造都不完整）
    for cap in SHARED_CAPABILITIES:
        assert hasattr(learn, cap), f"learn 缺少共享能力 {cap!r}"


def test_require_capability_raises_for_both_modes() -> None:
    """★ 双向：story 与 learn 取 learn 专属能力**都**抛错，且信息可操作。"""
    for mode in (mode_for(BookMode.STORY), make_learn_mode()):
        for cap in LEARN_ONLY_CAPABILITIES:
            with pytest.raises(CapabilityNotAvailable) as ei:
                require_capability(mode, cap)
            msg = str(ei.value)
            assert cap in msg, "错误信息必须指出**哪个能力**"
            assert mode.name in msg, "错误信息必须指出**哪个模式**"
            assert "实现" in msg, "错误信息必须给出**怎么办**"


def test_capability_error_is_a_notimplementederror() -> None:
    """`CapabilityNotAvailable` 必须继承 `NotImplementedError`。

    这样只捕获后者的既有调用方也能工作（向后兼容），
    同时新代码可以捕获更具体的类型拿额外信息。
    """
    assert issubclass(CapabilityNotAvailable, NotImplementedError)


def test_mode_for_covers_every_book_mode() -> None:
    """★ `BookMode` 的每个成员都必须能取到实现（防漏登记）。

    同类缺陷实测过一次：`PipelineStage.CHAPTER` 在枚举里有、
    在映射里没有 → 派发即 `KeyError`（阶段 0 修过）。
    """
    for m in BookMode:
        mode = mode_for(m)
        assert mode.name == m.value, f"{m.value} 取到的模式名是 {mode.name!r}"


def test_mode_for_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="未知模式"):
        mode_for("不存在的模式")


def test_mode_for_accepts_string_and_enum() -> None:
    """字符串与枚举都要能用（CLI 传字符串、内部传枚举）。"""
    assert mode_for("story").name == "story"
    assert mode_for(BookMode.STORY).name == "story"


# --------------------------------------------------------------------------- #
# 分层：modes/ 不得依赖 infra.llm 或 graph
# --------------------------------------------------------------------------- #


def test_modes_layer_does_not_import_llm_or_graph(repo_root) -> None:
    """★ `modes/` 不得 import `infra.llm` 或 `graph`（分层单向依赖）。

    模式层回答"这个模式给什么约束"，不负责"怎么调模型"
    （那是 `infra/llm` 的事）也不依赖图（图反向依赖模式层）。

    ⚠️ 允许 import `infra.prompts`（模板加载）——
    它不含 LLM 调用，只是文件读取。
    """
    forbidden = ("storyforge.infra.llm", "storyforge.graph")
    offenders: list[str] = []
    modes_dir = repo_root / "src" / "storyforge" / "modes"
    for path in sorted(modes_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(f) for f in forbidden):
                    offenders.append(
                        f"{path.relative_to(repo_root)}:{node.lineno} "
                        f"from {node.module}"
                    )
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if any(a.name.startswith(f) for f in forbidden):
                        offenders.append(
                            f"{path.relative_to(repo_root)}:{node.lineno} "
                            f"import {a.name}"
                        )
    assert not offenders, "modes/ 不得依赖 LLM 或 graph 层：\n  " + "\n  ".join(offenders)


def test_modes_layer_is_scanned_by_rule03(repo_root) -> None:
    """★ 反向自检：规则 3 的 `_MODE_SCAN_SKIP` 必须**真的**跳过 `modes/`。

    若 `modes/` 不在跳过名单里，规则 3 会把本层的 `mode_for()` 分派
    也当成违规 —— 那样"模式分支只许在 modes/"这条设计就无法落地，
    必然有人去放宽规则（而不是修代码）。
    """
    from test_architecture_invariants import _MODE_SCAN_SKIP

    assert "modes" in _MODE_SCAN_SKIP, (
        "`modes/` 必须在 `_MODE_SCAN_SKIP` 里 —— 它是模式分支的**唯一合法归属地**"
    )


# --------------------------------------------------------------------------- #
# PromptPack 的 pack 维度（learn 的 P1 单点前提）
# --------------------------------------------------------------------------- #


def test_load_supports_pack_dimension(repo_root) -> None:
    """★ `_load(name, pack=...)` 必须支持模式子目录。

    此前 `PROMPTS_DIR` 是**单一扁平目录、无模式维度** ——
    learn 的 `writer_system.md` **无处安放**（会与 story 撞名）。
    这是 learn prompt 能否落地的单点前提。
    """
    from storyforge.infra.prompts.library import _load

    # story（pack=""）走既有路径，内容非空
    story_sys = _load("writer_system")
    assert story_sys, "story 的 writer_system 不得为空"
    assert _load("writer_system", pack="") == story_sys, (
        "pack='' 必须与不传 pack 完全等价（既有 3 个调用点零改动）"
    )


def test_load_does_not_silently_fall_back_to_default_pack() -> None:
    """★★ 新模式 pack 缺失时必须**抛错**，不得静默回落 story 的模板。

    回落会让 learn 悄悄沿用"对话占比 ≥25%、比喻要少"的散文规范 ——
    而教学章"不要对话、比喻是教学手段"是**正确**写法。
    静默沿用比报错危险得多（旧的空实现教训）。
    """
    from storyforge.infra.prompts.library import _load

    with pytest.raises(FileNotFoundError) as ei:
        _load("writer_system", pack="不存在的模式")
    msg = str(ei.value)
    assert "pack" in msg, "报错必须点明是 pack 维度的问题"
    assert "不得" in msg or "回落" in msg, "报错必须说明**为什么不回落**"


def test_story_prompt_pack_dir_name_is_empty() -> None:
    """★ story 的 `dir_name` 必须是 `""` —— 保持既有扁平路径。

    改成 `"story"` 会让 prompt 目录结构变化，属于**行为变更**
    （模板文件要搬家），不该顺手做。
    """
    from storyforge.modes import StoryPromptPack

    assert StoryPromptPack().dir_name == ""


def test_learn_prompt_pack_dir_name_is_learn() -> None:
    """★ learn 的 `dir_name` 指出正确方向：`prompts/learn/`。"""
    from storyforge.modes import LearnPromptPack

    assert LearnPromptPack.dir_name == "learn"


def test_learn_prompt_pack_methods_raise() -> None:
    """★ learn 的 PromptPack 一期必须 raise，**不得返回空串**。

    返回空串会让 learn 静默沿用 story 的写作规范（两者直接冲突）。
    """
    from storyforge.modes import LearnPromptPack

    pack = LearnPromptPack()
    for meth in ("writer_system_extension", "extractor_instructions",
                 "critic_instructions"):
        with pytest.raises(NotImplementedError) as ei:
            getattr(pack, meth)()
        assert "实现" in str(ei.value), f"{meth} 的报错必须给出实现指引"

