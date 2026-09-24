"""learn 知识点核验（方案 B1）—— 防的是「自己批自己」与「核验形同虚设」。

## 本文件防的是什么（草稿 §4）

`doc/learn-验收标准-草稿.md` §4 的「知识准确性」是 P0 验收项。它此前
**根本无法执行**：系统分不清"资料支撑的知识点"与"模型编的"。

方案 B1 的要点是**核验侧必须与生成侧可区分**：
  · 生成侧：`mimo-v2.6-flash`（balanced）+ `LEARNING_MAP_SYSTEM`
  · 核验侧：`mimo-v2.6-pro`（premium）+ `VERIFY_SYSTEM`（独立 prompt）

★ 若两者相同，核验就退化成**自证**。故本文件钉住三件事：

  1. **prompt 独立**（不复用生成 prompt）+ 带 JSON 骨架
     —— 骨架漏登记已栽过**两次**（缺陷 11、`style_critic`）
  2. **路由可区分**（核验节点走 premium，生成节点走 balanced）
  3. **统计口径正确** —— `unsupported` 必须计入失败，
     空结果**不得**返回 1.0（假绿是本项目反复吃亏的形态）
"""

from __future__ import annotations

import re

import pytest

from storyforge.domain.enums import BookMode
from storyforge.infra.llm.learning_schemas import (
    LEARNING_MAP_SYSTEM,
    VERDICTS,
    VERIFY_SYSTEM,
    PointVerdictOut,
    VerificationOut,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 一、prompt：独立 + 带骨架
# --------------------------------------------------------------------------- #


def test_verify_prompt_has_json_skeleton() -> None:
    """★★ 核验 prompt 必须含 ```json 骨架。

    这条是缺陷 11 的直接教训，且**同类漏洞已栽过两次**：
    设计完 prompt 却没进 `STRUCTURED_NODES` → 长期无骨架而契约全绿。
    """
    blocks = re.findall(r"```json\s*(.+?)```", VERIFY_SYSTEM, re.S)
    assert blocks, "VERIFY_SYSTEM 没有 JSON 骨架"


def test_verify_prompt_declares_all_fields() -> None:
    """骨架必须逐字写出**全部字段名**（模型靠它对齐）。

    ★ 只写"给出判定"这类散文，模型会自造键名 —— 这正是缺陷 11 的成因。
    """
    block = re.findall(r"```json\s*(.+?)```", VERIFY_SYSTEM, re.S)[0]
    for field in ("point", "verdict", "reason", "evidence_index"):
        assert field in block, f"骨架里缺字段 {field!r}"


def test_verify_prompt_is_independent_from_generation() -> None:
    """★ 核验 prompt **不得**复用生成 prompt（防自证的第一层）。

    ★ 为什么钉这条：若核验用同一个 prompt，模型会顺着生成时的思路
    自我确认 —— 那核验就只是把生成结果抄了一遍。
    """
    assert VERIFY_SYSTEM != LEARNING_MAP_SYSTEM
    # 核验 prompt 不该出现生成侧的产出指令
    assert "knowledge_points" not in VERIFY_SYSTEM, "核验 prompt 混入了生成侧的字段"
    # 核验的核心动作是"判定"，不是"生成"
    assert "核查" in VERIFY_SYSTEM or "判定" in VERIFY_SYSTEM


def test_verify_prompt_states_three_states() -> None:
    """三态必须在 prompt 里**显式列出**，且 `inferred ≠ unsupported`。

    ★ 这是设计要点：教学需要"把资料讲透"，合理推演是必要的。
    一刀切要求"逐字有据"会让地图退化成资料摘抄；
    而把 `inferred` 当 `unsupported` 会**误杀**合格知识点。
    """
    for v in VERDICTS:
        assert v in VERIFY_SYSTEM, f"三态 {v!r} 未在 prompt 中说明"
    assert "inferred" in VERIFY_SYSTEM and "不是" in VERIFY_SYSTEM


def test_verify_prompt_asks_for_strictness() -> None:
    """prompt 要**明确要求严格**（不许为了让人通过而放宽）。

    ★ 核验节点最大的失败模式不是"判错"，而是"全判通过"——
    那样它就成了一个恒真断言（项目已记录：抓不到问题的检查等于没有）。
    """
    assert "严格" in VERIFY_SYSTEM or "宁可" in VERIFY_SYSTEM
    assert "查出问题" in VERIFY_SYSTEM or "不要放过" in VERIFY_SYSTEM


# --------------------------------------------------------------------------- #
# 二、schema：三态、归一、统计口径
# --------------------------------------------------------------------------- #


def test_verdict_normalizes_chinese_and_case() -> None:
    """归一：中文/大小写/近义写法都要接受（输入宽容）。

    与项目既有原则一致 —— **对输入宽容，对输出严格**。
    模型写「支持」或「SUPPORTED」都是自然的，形状漂移不该让整轮失败。
    """
    out = VerificationOut.model_validate({"verdicts": [
        {"point": "甲", "verdict": "支持"},
        {"point": "乙", "verdict": "SUPPORTED"},
        {"point": "丙", "verdict": "无据"},
        {"point": "丁", "verdict": "推断"},
    ]})
    assert [v.verdict for v in out.verdicts] == [
        "supported", "supported", "unsupported", "inferred",
    ]


def test_unknown_verdict_is_not_silently_passed() -> None:
    """★ 未知判定**不得**被硬塞成 `supported`。

    ★ 为什么钉这条：把"判不了"伪装成"通过"就是**给假绿** ——
    而假绿是本项目反复吃亏的形态（`entry_state` 填充率 13% 无人发现、
    `validate_map` 只查"有没有"不查"对不对"）。
    """
    out = VerificationOut.model_validate({"verdicts": [
        {"point": "甲", "verdict": "大概吧"},
    ]})
    assert out.verdicts[0].verdict == "大概吧"      # 原样保留，不伪装
    assert out.counts().get("unknown") == 1          # 单独计数
    assert out.accuracy() == 0.0                     # 不计入通过


def test_shape_tolerance_bare_list_and_single_object() -> None:
    """形状归一：裸列表、单条对象都要接受（归一，不拒绝）。"""
    a = VerificationOut.model_validate([{"point": "甲", "verdict": "supported"}])
    b = VerificationOut.model_validate({"verdicts": {"point": "甲", "verdict": "supported"}})
    assert len(a.verdicts) == 1
    assert len(b.verdicts) == 1


def test_empty_result_accuracy_is_zero_not_one() -> None:
    """★★ 空结果准确率必须是 **0.0**，不是 1.0。

    ★ 这是「假绿」防线：没有判定 ≠ 全部通过。
    若返回 1.0，核验节点一旦空输出就会被当成"知识点全对"。
    """
    assert VerificationOut().accuracy() == 0.0


def test_accuracy_counts_inferred_as_pass() -> None:
    """`inferred` 计入通过（草稿 §4.3 口径：supported + inferred）。"""
    out = VerificationOut.model_validate({"verdicts": [
        {"point": "a", "verdict": "supported"},
        {"point": "b", "verdict": "inferred"},
        {"point": "c", "verdict": "unsupported"},
        {"point": "d", "verdict": "supported"},
    ]})
    assert out.accuracy() == pytest.approx(0.75)
    assert out.counts()["unsupported"] == 1


def test_consecutive_unsupported_detects_runs_not_totals() -> None:
    """★ 硬门禁判"**连续**"而非"总数"（草稿 §4.3）。

    ★ 为什么区分：集中幻觉比分散更危险 ——
    连续 3 条说明模型在某个方向上**整段编造**，而非个别失误。
    若只数总数，5 条分散的 `unsupported`（中间夹着通过）与
    连续 3 条会得到同样处置，失去区分力。
    """
    scattered = VerificationOut.model_validate({"verdicts": [
        {"point": "a", "verdict": "unsupported"},
        {"point": "b", "verdict": "supported"},
        {"point": "c", "verdict": "unsupported"},
        {"point": "d", "verdict": "supported"},
        {"point": "e", "verdict": "unsupported"},
    ]})
    assert scattered.consecutive_unsupported() == 1, "分散的不该算连续"

    run = VerificationOut.model_validate({"verdicts": [
        {"point": "a", "verdict": "supported"},
        {"point": "b", "verdict": "unsupported"},
        {"point": "c", "verdict": "unsupported"},
        {"point": "d", "verdict": "unsupported"},
    ]})
    assert run.consecutive_unsupported() == 3


def test_evidence_index_normalizes() -> None:
    """`evidence_index` 归一：裸 int / 数字字符串 / 列表都要接受。"""
    v = PointVerdictOut.model_validate(
        {"point": "甲", "verdict": "supported", "evidence_index": "2"})
    assert v.evidence_index == [2]
    v2 = PointVerdictOut.model_validate(
        {"point": "甲", "verdict": "supported", "evidence_index": [1, "3"]})
    assert v2.evidence_index == [1, 3]


# --------------------------------------------------------------------------- #
# 三、★ 路由：核验侧必须与生成侧可区分（防自己批自己）
# --------------------------------------------------------------------------- #


def test_verify_node_routes_to_different_tier_than_generation() -> None:
    """★★ 核验节点与生成节点**必须落到不同模型**。

    ★ 这是方案 B1 的**全部要点**：同模型核验 = 自己批自己 = 自证。
    实测约束是「只配了 MIMO_API_KEY」，跨厂商（B3）做不了，
    故退而取**同厂不同档位**（premium vs balanced）。

    ★ 用 `route()` 而非硬编码模型名比对 —— 决策 D-09：
    将来配上 DEEPSEEK_API_KEY 升级到 B3 时，本测试**自动跟着变**
    （只要两侧仍可区分），不需要改测试。
    """
    from storyforge.infra.llm.capability import load_registry

    reg = load_registry()
    gen_model, _ = reg.route("learning_map")
    verify_model, _ = reg.route("learning_verify")
    assert gen_model.id != verify_model.id, (
        f"核验与生成用了同一个模型（{gen_model.id}）—— 方案 B1 退化为自证"
    )
    assert verify_model.tier != gen_model.tier, (
        "两侧档位相同，说明没有做档位隔离"
    )


def test_verify_node_is_registered_in_providers_toml() -> None:
    """核验节点必须在 `providers.toml` 登记能力需求（D-09）。

    ★ 为什么钉：本轮（2026-09-18）实测过同类缺陷 ——
    learn 三节点**忘记登记**，LLM 路径首次真跑即被
    `ProviderConfigError` 拦下。离线路径不经过路由器，所以一直没发现。
    """
    from storyforge.infra.llm.capability import ProviderConfigError, load_registry

    reg = load_registry()
    try:
        reg.route("learning_verify")
    except ProviderConfigError as exc:  # pragma: no cover - 明确的失败路径
        pytest.fail(f"learning_verify 未在 providers.toml 登记：{exc}")


# --------------------------------------------------------------------------- #
# 四、节点：输入构造（资料重新编号是易错点）
# --------------------------------------------------------------------------- #


async def test_verify_node_renumbers_snippets_consistently() -> None:
    """★★ 核验 prompt 里的资料编号，必须与模型回的 `evidence_index` 同坐标系。

    ★ 这是本节点**最容易写错**的地方：生成侧 prompt 给资料编了 `[N]`，
    核验侧的 prompt 是**另一段文本**。若两边编号规则不一致，
    模型回的编号会**静默指向错误的资料**（看起来有编号、其实对不上）。

    本测试用一个"记号 URL"的假 client，断言核验 prompt 里的第 N 条
    与传入 snippets 的第 N 条一致。
    """
    from storyforge.graph.nodes.learning import verify_learning_map

    captured: dict[str, str] = {}

    class _Call:
        from_cache = False

    class _FakeClient:
        async def complete_structured(self, *, node, system, user, schema, **kw):
            captured["node"] = node
            captured["system"] = system
            captured["user"] = user
            return VerificationOut.model_validate({"verdicts": []}), _Call()

    snippets = [
        {"title": "第一条资料", "text": "甲甲甲", "url": "https://ex/1"},
        {"title": "第二条资料", "text": "乙乙乙", "url": "https://ex/2"},
    ]
    await verify_learning_map(
        _FakeClient(), topic="测试主题",
        points=[("知识点A", "说明A"), ("知识点B", "说明B")],
        snippets=snippets, book_id=1,
    )

    assert captured["node"] == "learning_verify"
    # 资料按 1..N 编号，且顺序与传入一致
    assert "[1] 第一条资料" in captured["user"]
    assert "[2] 第二条资料" in captured["user"]
    # 待核验知识点也要列全（否则模型会漏判）
    assert "知识点A" in captured["user"] and "知识点B" in captured["user"]
    # 独立 prompt（不是生成侧那个）
    assert captured["system"] == VERIFY_SYSTEM


async def test_verify_node_passes_book_id_and_idempotency() -> None:
    """book_id 与 idempotency_key 必须传下去（缓存口径与其它节点一致）。"""
    from storyforge.graph.nodes.learning import verify_learning_map

    seen: dict[str, object] = {}

    class _Call:
        from_cache = False

    class _FakeClient:
        async def complete_structured(self, *, node, system, user, schema, **kw):
            seen.update(kw.get("context").__dict__ if kw.get("context") else {})
            return VerificationOut.model_validate({"verdicts": []}), _Call()

    await verify_learning_map(
        _FakeClient(), topic="t", points=[("a", "")], snippets=[],
        book_id=42, idempotency_key="learn:verify:42:t",
    )
    assert seen.get("book_id") == 42
    assert seen.get("idempotency_key") == "learn:verify:42:t"


def test_learn_mode_is_not_hardcoded_in_verify_path() -> None:
    """契约规则 3：不得按模式分支（`BookMode` 只作枚举存在，不参与判断）。

    ★ 本项目已记录：旧项目「禁止在共享 pipeline 加 `is_story` 分支」的规约
    被违反 **30+ 处**。learn 与 story **共用**核验节点是对的，
    但不得在节点内部写 `if mode == learn`。
    """
    from pathlib import Path

    src = Path("src/storyforge/graph/nodes/learning.py").read_text(encoding="utf-8")
    assert "BookMode" not in src, "learn 节点里出现了模式分支的迹象"
    # 枚举本身仍应可用（只是不该在这个路径上分支）
    assert BookMode.LEARN.value == "learn"
