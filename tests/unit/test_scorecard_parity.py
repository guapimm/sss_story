"""★ 重构等价性：把**重构前**的 story 打分行为固化进测试。

## 为什么必须这么做

2026-09-24 把 `aggregate.py` 的维度从「硬编码在函数体」抽成
`DimensionSpec` 表驱动（learn 打分卡的前提，审计 §4.4 第 8 条）。
那是**共享代码** —— 改错了会**静默**改变 story 的打分行为。

本项目对这类重构有明确纪律：拆 `doctor.py` 时凭记忆重写
**引入两个真 bug**，靠 `doctor: FAIL` 才发现。故必须逐字段验证等价。

## 快照怎么来的

`scripts/capture_scorecard_parity.py` 在**重构前**运行，
输出落在 `tests/golden/scorecard_story_baseline.json`（已入库）。
本测试重跑同样的输入，断言**逐字段一致**。

★ 复现方式（快照需更新时）：
    1. `git stash`（回到重构前）
    2. `uv run python scripts/capture_scorecard_parity.py`
    3. `git stash pop`

## ★ 这套断言真的抓到过一次回归

初版我把 `unsupported_entities` 写成返回**布尔**，而原实现报的是
**条数**（`float(len(...))`）→ 该维度的 `value` 从 `0.0` 变成 `None`
（**静默丢掉指标值**），而 `passed` 恰好不变、**总分毫无变化**。
只测总分是抓不到的 —— 这正是"逐维度断言"的必要性。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

#: ★ 重构前快照（由 `scripts/capture_scorecard_parity.py` 生成）
SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "tests" / "golden" / "scorecard_story_baseline.json"
)

#: 结构层维度名（仅在提供细纲时出现）
STRUCTURAL_DIMS = (
    "outline_adherence", "hook_landed", "state_consistency",
    "unsupported_entities",
)


# --------------------------------------------------------------------------- #
# 输入（与 capture 脚本一致 —— 两边不同会让等价性证明失效）
# --------------------------------------------------------------------------- #

GOOD = (
    "“你来了。”老者抬眼，声音沙哑。\n"
    "林默没有立刻答话。他把那枚残破的玉牌放在石桌上，指尖在边缘停了半秒。"
    "屋外风穿过廊下，吹得灯焰歪向一侧，又慢慢立直。\n"
    "“我不明白。”他终于说，“师父当年为什么不带我走？”\n"
    "老者笑了笑,没答。他伸手把玉牌翻了个面，背面刻着极浅的纹路，"
    "像水波，又像某个人收紧的手指。\n"
    "“有些事，不是不带，是带不走。”他说，“你如今站到这里，"
    "说明你已经把该问的都问过一遍了。”\n"
    "林默沉默。他忽然想起十二岁那年的雨，想起自己被留在门槛外的那一夜。"
    "记忆像钝刀，割不破皮肉，却一直在那里压着。\n"
    "“那我该做什么。”他问。\n"
    "“回去。”老者说，“回青云宗。把你在外面学的东西，一样一样放下。”\n"
    "灯焰又晃了一下。这一次，它没有再立起来。\n"
) * 3

BAD = "他走了。他停下。他回头。他看了看。他离开了。他继续走。他什么都没有说。"

CLICHE_HEAVY = (
    "他嘴角勾起一抹笑意，眼中闪过一丝精光。仿佛时间仿佛静止，"
    "不禁下意识地心头一紧。霎时，顿时，旋即，随即，不由自主地，"
    "他知道，这一刻，命运的齿轮开始转动。新的篇章就此开启。"
) * 2

INPUTS: dict[str, tuple[str, bool]] = {
    "good_no_plan": (GOOD, False),
    "bad_no_plan": (BAD, False),
    "cliche_no_plan": (CLICHE_HEAVY, False),
    "good_with_plan": (GOOD, True),
    "bad_with_plan": (BAD, True),
}


def _plan():
    from storyforge.domain.outline import ChapterPlan, SceneBeat

    return ChapterPlan(
        global_no=1, volume_no=1, title="测试章",
        plot="林默向老者求证当年的事",
        scenes=[
            SceneBeat(seq=0, location_name="石桌",
                      participant_names=["林默", "老者"]),
            SceneBeat(seq=1, location_name="回忆中的雨夜",
                      participant_names=["林默"]),
        ],
        end_hook="老者说：回青云宗，把学的东西放下",
    )


def _eval(prose: str, *, with_plan: bool):
    from storyforge.scorecard import evaluate

    return evaluate(
        prose, global_no=5, plan=_plan() if with_plan else None,
        prev_texts=[GOOD[:2000]] if with_plan else None,
    )


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict]:
    assert SNAPSHOT.exists(), (
        f"缺重构前快照 {SNAPSHOT}。"
        f"生成方式见本文件 docstring（需先 git stash 回到重构前）。"
    )
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {c["label"]: c for c in data}


# --------------------------------------------------------------------------- #
# 断言
# --------------------------------------------------------------------------- #


def test_snapshot_covers_all_cases(baseline: dict) -> None:
    """快照必须覆盖全部用例 —— 否则"等价性"只是部分证明。"""
    assert set(baseline) == set(INPUTS), (
        f"快照与测试的用例集合不一致："
        f"缺 {sorted(set(INPUTS) - set(baseline))}，"
        f"多 {sorted(set(baseline) - set(INPUTS))}"
    )


@pytest.mark.parametrize("label", sorted(INPUTS))
def test_story_score_unchanged(label: str, baseline: dict) -> None:
    """★ 总分 / passed / 触发项 **逐字段不变**。"""
    prose, with_plan = INPUTS[label]
    card = _eval(prose, with_plan=with_plan)
    exp = baseline[label]

    assert round(card.score, 6) == pytest.approx(exp["score"], abs=1e-6), (
        f"{label} 总分变了：{card.score} != {exp['score']}"
    )
    assert card.passed == exp["passed"], f"{label} passed 变了"
    assert card.needs_review == exp["needs_review"], f"{label} needs_review 变了"
    assert card.forced_review == exp["forced_review"], f"{label} forced_review 变了"
    assert sorted(t.value for t in card.triggers) == exp["triggers"], (
        f"{label} 触发项变了："
        f"{sorted(t.value for t in card.triggers)} != {exp['triggers']}"
    )


@pytest.mark.parametrize("label", sorted(INPUTS))
def test_story_dimensions_unchanged(label: str, baseline: dict) -> None:
    """★★ **逐维度**的 value / passed / weight 都不变。

    ★ 为什么必须逐维度：加权总分可能**掩盖**两个维度的反向变化
    （一个变严、一个变松，总分恰好持平）。
    本测试实际抓到过一次 —— `unsupported_entities` 的 `value`
    从 `0.0` 掉成 `None`（指标值静默丢失），而 `passed` 与总分都没变。
    """
    prose, with_plan = INPUTS[label]
    card = _eval(prose, with_plan=with_plan)
    got = {d.name: d for d in card.dimensions}

    exp_dims = {d[0]: d for d in baseline[label]["dimensions"]}
    assert set(got) == set(exp_dims), (
        f"{label} 维度集合变了："
        f"多 {sorted(set(got) - set(exp_dims))}，"
        f"少 {sorted(set(exp_dims) - set(got))}"
    )

    for name, (_n, exp_val, exp_ok, exp_w) in exp_dims.items():
        d = got[name]
        assert d.weight == exp_w, f"{label}.{name} 权重变了 {d.weight} != {exp_w}"
        assert d.passed == exp_ok, (
            f"{label}.{name} passed 变了 {d.passed} != {exp_ok}"
        )
        if exp_val is None:
            assert d.value is None, (
                f"{label}.{name} value 应为 None，实为 {d.value}"
            )
        else:
            assert d.value == pytest.approx(exp_val, abs=1e-6), (
                f"{label}.{name} value 变了 {d.value} != {exp_val}"
            )


@pytest.mark.parametrize("label", ["good_no_plan", "bad_no_plan", "cliche_no_plan"])
def test_structural_dimensions_absent_without_plan(label: str) -> None:
    """★ 无细纲时结构层维度**必须不产出**（不是产出 0 分）。

    ★ 这条对应审计缺陷 6：`analyze_structural(prose, None)` 返回
    **空白 `StructuralMetrics`（非 None）**，于是结构层 4 维
    `passed=True/None` **白送 2.1 分**，权重分母 10.0→15.3，门槛实质失效。
    故 `evaluate` 在无 plan 时必须传 `st=None`。
    """
    prose, _ = INPUTS[label]
    names = {d.name for d in _eval(prose, with_plan=False).dimensions}
    for sname in STRUCTURAL_DIMS:
        assert sname not in names, f"无细纲却产出了结构维度 {sname}"


@pytest.mark.parametrize("label", ["good_with_plan", "bad_with_plan"])
def test_structural_dimensions_present_with_plan(label: str) -> None:
    """有细纲时结构层 4 维必须出现（否则权重分母不对 → 分数被抬高）。"""
    prose, _ = INPUTS[label]
    names = {d.name for d in _eval(prose, with_plan=True).dimensions}
    for sname in STRUCTURAL_DIMS:
        assert sname in names, f"有细纲却缺结构维度 {sname}"


# --------------------------------------------------------------------------- #
# spec 表自身的静态断言
# --------------------------------------------------------------------------- #


def test_story_dimension_table_order() -> None:
    """★ `STORY_DIMENSIONS` 的**顺序**也要钉住。

    ★ 顺序会出现在报告里；且"漏一个维度"会让权重分母变小
    → 分数被抬高（**静默变松**）。
    """
    from storyforge.scorecard.dimensions import STORY_DIMENSIONS

    assert [s.name for s in STORY_DIMENSIONS] == [
        "dialogue_ratio", "cross_chapter_overlap", "intra_redundancy",
        "cliche_hits", "simile_count", "strong_emotion_words",
        "sentence_len_cv", "chars", "not_time_skip_start", "ending_pattern",
        "outline_adherence", "hook_landed", "state_consistency",
        "unsupported_entities",
    ]


def test_story_dimension_weights_unchanged() -> None:
    """★ 权重表逐项不变（权重是总分的分子与分母，改一个整体漂移）。"""
    from storyforge.scorecard.dimensions import STORY_DIMENSIONS

    assert {s.name: s.weight for s in STORY_DIMENSIONS} == {
        "dialogue_ratio": 1.5,
        "cross_chapter_overlap": 1.5,
        "intra_redundancy": 1.0,
        "cliche_hits": 1.0,
        "simile_count": 1.0,
        "strong_emotion_words": 0.8,
        "sentence_len_cv": 0.7,
        "chars": 1.2,
        "not_time_skip_start": 0.5,
        "ending_pattern": 0.8,
        "outline_adherence": 2.0,
        "hook_landed": 1.2,
        "state_consistency": 1.5,
        "unsupported_entities": 0.6,
    }


def test_every_spec_metric_is_registered() -> None:
    """spec 引用的访问器必须都已登记（否则运行期 `KeyError`）。"""
    from storyforge.scorecard.dimensions import ACCESSORS, STORY_DIMENSIONS

    for spec in STORY_DIMENSIONS:
        assert spec.metric in ACCESSORS, (
            f"维度 {spec.name!r} 引用了未登记的访问器 {spec.metric!r}"
        )


def test_every_spec_target_exists_on_thresholds() -> None:
    """spec 声明的**阈值字段**必须在 `Thresholds` 上存在。

    ★ 拼错字段名会让该维度**静默失效**（`build_dimensions_from_specs`
    会抛 `AttributeError`，但那是运行期 —— 本测试让它提前到静态检查）。
    """
    from storyforge.scorecard.aggregate import Thresholds
    from storyforge.scorecard.dimensions import STORY_DIMENSIONS

    th = Thresholds()
    for spec in STORY_DIMENSIONS:
        if spec.target is not None:
            assert hasattr(th, spec.target), (
                f"维度 {spec.name!r} 的阈值字段 {spec.target!r} 不存在"
            )
