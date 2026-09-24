"""learn 打分卡测试 —— 防的是「用 story 的尺子量教学章」（阶段 2-5）。

## 本文件防的是什么（实测）

用 story 卡评一篇**标准说明文** → `score=0.51 / passed=False`，
`triggers=[golden_three, dialogue_low, sentence_uniform, too_short, low_score]`。
其中 **`dialogue_low` 是硬触发** —— 而教学章「不要对话」是**正确**写法。

⇒ 不是调参问题，是**维度集合错配**。故 learn 需要自己的打分卡。

## 本文件钉住四件事

1. **不误杀**：合格教学章不得出现 `dialogue_low` / `golden_three` /
   `ending_omen`（这三项对 learn 是**语义错误**，不是阈值问题）
2. **真能抓**：直出原术语 / 灌水 / 只提名词不讲透 / 复习点漏讲
   必须各有对应触发（否则 learn 卡也是"抓不到问题"的摆设）
3. **口径正确**：空分母 → `None`（无法评估）而不是 0 或 1（假绿/假红）
4. **平替检测的边界**：说法含术语、部分匹配这两个真实坑
"""

from __future__ import annotations

import pytest

from storyforge.scorecard.learn import (
    LEARN_DIMENSIONS,
    LearnMetrics,
    LearnThresholds,
    count_bare_terms,
    count_term_usage,
    evaluate_learn,
    knowledge_density,
)

pytestmark = pytest.mark.unit


#: 一篇「合格的教学章」文本骨架：无对话、有总结收尾、句长有变化。
#: ★ 刻意**不含**任何 story 喜欢的东西（对话/钩子）——
#: 这正是要证明"learn 不因缺少它们而扣分"。
TEACHING_PROSE = (
    "本讲讲的是光合作用的基本原理。"
    "叶绿体是发生这一过程的场所，它含有的叶绿素能吸收红光与蓝紫光。"
    "整个反应分为两个阶段，前者需要光照，后者不需要。"
    "前者在类囊体薄膜上进行，生成能量分子与还原剂；"
    "后者在基质中进行，利用前者提供的能量固定二氧化碳。"
    "两步彼此依赖，缺一不可，但分工明确。"
    "理解这个分工，是理解后续内容的前提。"
    "综上所述，光合作用是生态系统能量流动的基础。"
) * 3

MAPPINGS: list[tuple[str, str]] = [
    ("光合作用", "灵植化育"),
    ("叶绿体", "碧叶灵府"),
]


def _good_metrics() -> LearnMetrics:
    return LearnMetrics(
        bare_term_hits=0,
        substitution_hits=2, substitution_expected=2,
        points_mentioned=4, points_explained=4,
        progression_ok=3, progression_expected=3,
        review_injected=2, review_due=2,
    )


# --------------------------------------------------------------------------- #
# 一、★ 不误杀（核心：这是 2-5 存在的理由）
# --------------------------------------------------------------------------- #


def test_teaching_chapter_has_no_story_only_triggers() -> None:
    """★★ 合格教学章**不得**被 story 的专属判据触发。

    ★ 这是本项存在的全部理由。实测 story 卡会给出
    `dialogue_low`（硬触发）+ `golden_three` + `ending_omen` ——
    而教学章本就少对话、无黄金三章、必须有总结收尾。

    若这条测试红了，说明 learn 卡退回了 story 的维度集合。
    """
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=_good_metrics())
    got = {t.value for t in card.triggers}

    for forbidden in ("dialogue_low", "golden_three", "ending_omen"):
        assert forbidden not in got, (
            f"教学章被 {forbidden!r} 误杀 —— 该判据对 learn 语义错误"
        )


def test_no_forced_review_for_learn() -> None:
    """learn **没有**「黄金三章」概念（那是网文留存策略）。"""
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=_good_metrics())
    assert card.forced_review is False


def test_summary_ending_is_not_penalized() -> None:
    """★ 教学章的**总结式收尾是必需**，不得判罚。

    ★ story 把 `SUMMARY_ENDINGS` 列为可疑（"终于/明白了/从此以后"）；
    learn 里「综上所述」「回顾本讲」是**教学标准写法** ——
    实测这句话在 story 的 `CLICHES:86` 里被当套话。
    """
    text = "综上所述，本讲的内容可以归纳为三点。" + TEACHING_PROSE
    card = evaluate_learn(text, global_no=2, metrics=_good_metrics())
    assert "cliche_heavy" not in {t.value for t in card.triggers}
    assert "ending_omen" not in {t.value for t in card.triggers}


def test_low_dialogue_is_not_penalized() -> None:
    """★ 对话占比低 **不是** learn 的问题（story 里是硬触发）。

    这里用「零对话」的纯讲解文，断言不触发任何对话相关项；
    同时验证对话占比**过高**才会被上限维度扣分。
    """
    card = evaluate_learn(TEACHING_PROSE, global_no=3, metrics=_good_metrics())
    assert "dialogue_low" not in {t.value for t in card.triggers}

    # 对话占比维度的语义是**上限**：超过 ceiling 才不通过
    dim = next(d for d in card.dimensions if d.name == "dialogue_ceiling")
    assert dim.passed is True, "无对话的教学章不该在对话上限维度失败"


# --------------------------------------------------------------------------- #
# 二、真能抓（否则 learn 卡也是摆设）
# --------------------------------------------------------------------------- #


def test_bare_term_is_caught() -> None:
    """★ 直出原术语 → `bare_term`（learn 专属硬触发）。

    ★ 这是 L-2 逐条确认的**全部意义**：确认了映射却直出原术语，
    读者会看到「函数」「递归」这种出戏词。

    ★★ 这里用**字面常量 3**，不用 `LearnThresholds().hard_bare_term_hits`：
    初版我读了阈值来构造输入，于是「把阈值改成 999（永不触发）」这种
    真实退化**测不出来**（输入跟着阈值一起变，断言恒成立）。
    被 `scripts/mutate_learn_scorecard.py` 的变异 2 拆穿 ——
    **自适应的测试等于没测**。
    """
    m = _good_metrics()
    m.bare_term_hits = 3              # 恰好达到硬门禁（阈值就是 3）
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=m)
    assert "bare_term" in {t.value for t in card.triggers}

    # 同时也钉住"阈值确实是 3"——阈值被放宽时这条会红
    assert LearnThresholds().hard_bare_term_hits == 3, (
        "hard_bare_term_hits 被改动了 —— 若是有意放宽，请同时更新本测试并说明原因"
    )


def test_low_substitution_rate_is_caught() -> None:
    """平替命中率过低 → `substitution_low`。"""
    m = _good_metrics()
    m.substitution_hits, m.substitution_expected = 1, 10
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=m)
    assert "substitution_low" in {t.value for t in card.triggers}


def test_low_density_is_caught() -> None:
    """★ 灌水（知识密度过低）→ `low_density`。

    ★ 注意 `lit.chars` 是**空白归一后**的字数（`norm_whitespace`），
    不是 `len(prose)` —— 写测试时踩过：以为 `TEACHING_PROSE` 有 3000+ 字，
    实际归一后只有 528 字，于是"1 个知识点"反而密度很高。
    故这里用**长正文 + 极少知识点**构造真实的灌水场景。
    """
    m = _good_metrics()
    m.points_mentioned = 1
    # 灌水：把正文拉长到 ~10k 字，却只承载 1 个知识点
    padded = TEACHING_PROSE + ("这里是一些与知识点无关的铺陈文字。" * 200)
    card = evaluate_learn(padded, global_no=1, metrics=m)
    assert "low_density" in {t.value for t in card.triggers}


def test_shallow_explanation_is_caught() -> None:
    """★ 只提名词不讲透 → `explanation_shallow`（防列举式教学）。"""
    m = _good_metrics()
    m.points_mentioned, m.points_explained = 10, 1
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=m)
    assert "explanation_shallow" in {t.value for t in card.triggers}


def test_missed_review_is_caught() -> None:
    """★ 到期复习点未被讲到 → `review_missed`（结合 2-4 教学账本）。"""
    m = _good_metrics()
    m.review_injected, m.review_due = 0, 4
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=m)
    assert "review_missed" in {t.value for t in card.triggers}


def test_short_chapter_is_caught() -> None:
    """篇幅不足 → `too_short`（教学章太短讲不透）。"""
    card = evaluate_learn("太短了。", global_no=1, metrics=_good_metrics())
    assert "too_short" in {t.value for t in card.triggers}


# --------------------------------------------------------------------------- #
# 三、口径正确（防假绿 / 假红）
# --------------------------------------------------------------------------- #


def test_empty_denominator_returns_none_not_zero_or_one() -> None:
    """★★ 空分母 → `None`（无法评估），**不是** 0 也不是 1。

    ★ 两个方向的错都要防：
      · 返回 1.0 → 「没讲任何知识点」被当成"平替全对"（**假绿**）
      · 返回 0.0 → 被当成"全都直出术语"（**假红**，会误触发）

    这与 `VerificationOut.accuracy()` 的空结果口径一致
    （本项目反复吃亏于"空结果当成通过"）。
    """
    empty = LearnMetrics()
    assert empty.substitution_rate is None
    assert empty.explained_ratio is None
    assert empty.progression_rate is None
    assert empty.review_injection_rate is None
    assert knowledge_density(0, 0) is None
    assert knowledge_density(5, 0) is None


def test_empty_denominator_does_not_trigger() -> None:
    """★ 空分母**不得**触发（否则"本章没讲复习点"会被误判为漏讲）。

    场景：本章没有到期复习点（`review_due=0`）→ 不该触发 `review_missed`。
    """
    card = evaluate_learn(TEACHING_PROSE, global_no=1, metrics=LearnMetrics())
    got = {t.value for t in card.triggers}
    for v in ("review_missed", "explanation_shallow", "substitution_low"):
        assert v not in got, f"空分母却触发了 {v!r}"


def test_density_is_per_thousand_chars() -> None:
    """知识密度口径：每**千字**（不是每字、不是每百字）。"""
    assert knowledge_density(3, 1000) == pytest.approx(3.0)
    assert knowledge_density(3, 3000) == pytest.approx(1.0)
    assert knowledge_density(1, 500) == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# 四、平替修辞检测的边界（真实坑）
# --------------------------------------------------------------------------- #


def test_bare_term_detection_excludes_replacement() -> None:
    """★ 检测前必须**先剥掉世界内说法** —— 防"说法含术语"自我误报。"""
    maps = [("茶", "灵茶")]        # 说法**包含**术语
    n, _ = count_bare_terms("他修习灵茶。", maps)
    assert n == 0, "说法里的术语被误判为直出"


def test_bare_term_detection_requires_whole_term() -> None:
    """★ **部分匹配不算直出**。

    实测真实术语是 5~9 字的知识短语（「调膏与七汤注水」）。
    正文写「候汤最难」是在讲这个知识点（术语「候汤与熁盏」），
    若做部分匹配会**误报**。
    """
    maps = [("候汤与熁盏", "辨沸与炙盏")]
    n, _ = count_bare_terms("候汤最难，未熟则沫浮。", maps)
    assert n == 0, "部分匹配被误判为直出"


def test_bare_term_detection_finds_real_hits() -> None:
    maps = [("光合作用", "灵植化育"), ("叶绿体", "碧叶灵府")]
    n, hits = count_bare_terms("光合作用发生在叶绿体中。", maps)
    assert n == 2
    assert set(hits) == {"光合作用", "叶绿体"}


def test_term_usage_does_not_count_unmentioned() -> None:
    """★ 未提到的知识点**不计入分母**（否则是分母造假）。

    ★ 若把"本章没讲的知识点"也算进分母，任何一章命中率都会被拉低 ——
    这与 story 卡"47/47 章全触发"是同一类错误（闸门失去筛选意义）。
    """
    maps = [("光合作用", "灵植化育"), ("递归", "心镜回照")]
    # 只讲了第一个且用对 → 分母应为 1 而非 2
    hit, expected = count_term_usage("他修习灵植化育。", maps)
    assert (hit, expected) == (1, 1)

    # 只讲了第二个但直出 → 分母 1、命中 0
    hit2, exp2 = count_term_usage("他理解递归。", maps)
    assert (hit2, exp2) == (0, 1)

    # 两个都没讲 → 分母 0（→ 命中率为 None）
    hit3, exp3 = count_term_usage("完全无关的内容。", maps)
    assert (hit3, exp3) == (0, 0)


def test_term_usage_counts_bare_as_missed() -> None:
    """直出术语 = 明确没做对（计入分母、不计命中）。"""
    maps = [("点茶", "灵茶古法")]
    hit, expected = count_term_usage("他学习点茶。", maps)
    assert (hit, expected) == (0, 1)


# --------------------------------------------------------------------------- #
# 五、维度表自身
# --------------------------------------------------------------------------- #


def test_learn_dimensions_cover_user_approved_set() -> None:
    """★ 维度集合 = 用户 2026-09-24 拍板的 10 项（+章末时间硬切）。

    ★ 钉这条的理由：相邻的 story 卡有 14 维，很容易"顺手抄过来"，
    而其中 `dialogue_ratio`（下限）与 `ending_pattern` 对 learn 是**语义错误**。
    """
    names = {s.name for s in LEARN_DIMENSIONS}
    assert names == {
        "knowledge_density", "term_substitution", "explanation_depth",
        "progression", "review_injection", "intra_redundancy",
        "cross_chapter_overlap", "chars", "dialogue_ceiling",
        "simile_ceiling", "no_time_skip",
    }


def test_learn_has_no_story_only_dimensions() -> None:
    """★ learn 维度表**不得**含 story 专属维度名。

    这些名字一旦出现，说明有人把 story 的维度抄过来了 ——
    而它们的语义与 learn 冲突（见模块 docstring 的三处相反语义）。
    """
    names = {s.name for s in LEARN_DIMENSIONS}
    for story_only in ("golden_three", "hook_landed", "outline_adherence",
                       "ending_pattern", "not_time_skip_start",
                       "state_consistency", "unsupported_entities"):
        assert story_only not in names, f"learn 卡混入了 story 专属维度 {story_only!r}"


def test_learn_accessors_all_registered() -> None:
    """spec 引用的访问器必须都已登记。"""
    from storyforge.scorecard.learn import LEARN_ACCESSORS

    for spec in LEARN_DIMENSIONS:
        assert spec.metric in LEARN_ACCESSORS, (
            f"learn 维度 {spec.name!r} 引用了未登记的访问器 {spec.metric!r}"
        )


def test_learn_thresholds_all_exist() -> None:
    """spec 声明的阈值字段必须在 `LearnThresholds` 上存在（防拼错静默失效）。"""
    th = LearnThresholds()
    for spec in LEARN_DIMENSIONS:
        if spec.target is not None:
            assert hasattr(th, spec.target), (
                f"learn 维度 {spec.name!r} 的阈值字段 {spec.target!r} 不存在"
            )
