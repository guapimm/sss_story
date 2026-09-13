"""打分卡基线复现测试（P2 核心验收）。

★ 本文件的使命：**证明本实现能逐字节复现旧项目的口径**。

为什么这件事重要：只有口径一致，新系统与旧系统的质量对比才有效。
若口径不同（例如 4-gram 忘了保留标点），章内冗余率会从 6.2% 掉到 4.2%，
所有"我们变好了"的结论都失去意义。

基线来源：旧项目 `docs/prose_style_baseline_book23.json`，
由 `backend/app/prose_style.py` 在**书 23 的 47 章真实正文**上测得。

若本机没有旧项目数据，测试会 skip 而不是失败 —— 但**必须**在有数据时通过。
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

import pytest

from storyforge.scorecard import analyze, evaluate, Thresholds
from storyforge.scorecard.literal import (
    classify_ending,
    cross_chapter_overlap,
    dialogue_ratio,
    intra_redundancy,
    norm_whitespace,
    triple_parallelism_count,
)

pytestmark = pytest.mark.unit

#: 旧项目书 23 的正文目录（只读参照）
OLD_BOOK23_CHAPTERS = Path(
    r"E:\AI_project\story_llm\backend\storage\story\books\23\chapters"
)
OLD_BASELINE_JSON = Path(r"E:\AI_project\story_llm\docs\prose_style_baseline_book23.json")

#: 旧项目 baseline 的权威值（`prose_style_baseline_book23.json` 的 summary 段）
BASELINE = {
    "chars_p50": 5065,
    "dialogue_p50": 0.1058,
    "dialogue_min": 0.0147,
    "dialogue_max": 0.5994,
    "intra_p50": 0.0621,
    "intra_max": 0.1223,
    "cross_p50": 0.0985,
    "cross_max": 0.1451,
    "triple_total": 60,
    "triple_chapters": 33,
    "ending": {"mid_action": 23, "omen": 2, "other": 21, "dialogue": 1},
}


def _load_book23() -> list[str]:
    if not OLD_BOOK23_CHAPTERS.exists():
        pytest.skip("旧项目书 23 正文不可用（只读参照数据缺失）")
    files = sorted(
        OLD_BOOK23_CHAPTERS.glob("v1-*.txt"),
        key=lambda p: int(p.stem.split("-")[1]),
    )
    if len(files) < 40:
        pytest.skip(f"书 23 章节不足（实测 {len(files)} 章）")
    return [f.read_text(encoding="utf-8", errors="ignore") for f in files]


def _p50(xs) -> float:
    vals = [x for x in xs if x is not None]
    return statistics.median(vals) if vals else float("nan")


# --------------------------------------------------------------------------- #
# ★ 全量基线复现
# --------------------------------------------------------------------------- #


def test_reproduces_old_baseline_exactly() -> None:
    """★ 全部字面指标必须复现旧 baseline。

    这一条测试是整个 P2 的基石：它保证新旧对比有效。
    """
    texts = _load_book23()
    metrics = [
        analyze(t, prev_texts=texts[max(0, i - 5) : i]) for i, t in enumerate(texts)
    ]

    checks = [
        ("chars p50", _p50([m.chars for m in metrics]), BASELINE["chars_p50"], 1.0),
        (
            "dialogue p50",
            _p50([m.dialogue_ratio for m in metrics]),
            BASELINE["dialogue_p50"],
            0.0015,
        ),
        (
            "dialogue min",
            min(m.dialogue_ratio for m in metrics if m.dialogue_ratio is not None),
            BASELINE["dialogue_min"],
            0.0015,
        ),
        (
            "dialogue max",
            max(m.dialogue_ratio for m in metrics if m.dialogue_ratio is not None),
            BASELINE["dialogue_max"],
            0.0015,
        ),
        (
            "intra p50",
            _p50([m.intra_redundancy for m in metrics]),
            BASELINE["intra_p50"],
            0.0015,
        ),
        (
            "intra max",
            max(m.intra_redundancy for m in metrics if m.intra_redundancy is not None),
            BASELINE["intra_max"],
            0.0015,
        ),
        (
            "cross p50",
            _p50([m.cross_chapter_overlap for m in metrics]),
            BASELINE["cross_p50"],
            0.0015,
        ),
        (
            "cross max",
            max(
                m.cross_chapter_overlap
                for m in metrics
                if m.cross_chapter_overlap is not None
            ),
            BASELINE["cross_max"],
            0.0015,
        ),
    ]

    failures: list[str] = []
    for name, ours, want, tol in checks:
        if abs(ours - want) > tol:
            failures.append(f"{name}: ours={ours:.4f} baseline={want:.4f}")
    assert not failures, "口径与旧 baseline 不一致：\n  " + "\n  ".join(failures)


def test_reproduces_triple_parallelism_counts() -> None:
    """三段式排比：60 处 / 33 章。

    ⚠️ 这条曾经失败过 —— 初版用简单正则 `有的...,有的...` 只得到 2 处 / 2 章。
    正确口径是：连续 ≥3 个分句两两「同构」（完全相同/同头2字/同尾2字/Dice≥0.5）。
    """
    texts = _load_book23()
    counts = [triple_parallelism_count(t) for t in texts]
    total = sum(counts)
    chapters = sum(1 for c in counts if c)
    assert total == BASELINE["triple_total"], f"总数 {total} != 60"
    assert chapters == BASELINE["triple_chapters"], f"章数 {chapters} != 33"


def test_reproduces_ending_distribution() -> None:
    """章末类型分布必须完全一致：mid_action 23 / other 21 / omen 2 / dialogue 1。

    ★ 注意 `summary = 0` 与 `time_skip = 0`。
    这说明旧项目「不要总结式结尾」的规则**确实生效了** ——
    因此 `Thresholds` 对 `summary` **只观测不判罚**（否则会引入假阳性）。
    """
    texts = _load_book23()
    got = Counter(classify_ending(t).value for t in texts)
    want = BASELINE["ending"]
    for k, v in want.items():
        assert got.get(k, 0) == v, f"{k}: ours={got.get(k, 0)} baseline={v}"
    assert sum(got.values()) == 47
    # 明确断言：summary / time_skip 为 0
    assert got.get("summary", 0) == 0
    assert got.get("time_skip", 0) == 0


def test_baseline_json_matches_our_hardcoded_values() -> None:
    """若旧 baseline JSON 可用，核对本文件硬编码的值没有抄错。"""
    if not OLD_BASELINE_JSON.exists():
        pytest.skip("baseline JSON 不可用")
    data = json.loads(OLD_BASELINE_JSON.read_text(encoding="utf-8"))
    s = data["summary"]
    assert s["chars"]["p50"] == BASELINE["chars_p50"]
    assert round(s["dialogue_ratio"]["p50"], 4) == BASELINE["dialogue_p50"]
    assert round(s["intra_redundancy"]["p50"], 4) == BASELINE["intra_p50"]
    assert round(s["cross_chapter_overlap"]["p50"], 4) == BASELINE["cross_p50"]
    assert s["triple_parallelism_total"] == BASELINE["triple_total"]


# --------------------------------------------------------------------------- #
# 口径细节（每条防一个具体错误）
# --------------------------------------------------------------------------- #


def test_ngram_keeps_punctuation() -> None:
    """★ 4-gram 必须在「去空白但**保留标点**」的文本上切分。

    旧项目 `prose_style.py:26-27` 明确记载：
    去标点会让章内冗余率从 **6.2% 掉到 4.2%**，与 baseline 不符。
    原因是 `了一下。`、`。”陈砚` 这类**带标点**的填充片段才是有意义的信号。
    """
    text = "他走了。他走了。他走了。"
    assert norm_whitespace(text) == "他走了。他走了。他走了。"
    assert "。" in norm_whitespace(text), "标点必须保留"
    assert intra_redundancy(text) is not None


def test_cross_chapter_overlap_uses_sets_not_counts() -> None:
    """★ 跨章重叠用**集合**而非词频。

    旧项目实测：词频口径给出 11.7%，与 baseline 9.85% 不符。
    """
    cur = "甲乙丙丁戊己庚辛"
    prev = ["甲乙丙丁壬癸"]
    got = cross_chapter_overlap(cur, prev)
    # 集合口径：A = 本章 4-gram 集合（5 个），B = 前章集合（3 个）
    # 交集 = {甲乙丙丁} → 1/5 = 0.2
    assert got == pytest.approx(0.2)


def test_dialogue_ratio_regex_matches_old_caliber() -> None:
    """对话占比口径：`[“"「『]([^”"」』]{2,})[”"」』]`。"""
    text = "「这是一句对话。」他说完就离开了，没有回头。"
    quoted = len("这是一句对话。")
    assert dialogue_ratio(text) == pytest.approx(quoted / len(text.strip()))


def test_missing_inputs_return_none_not_zero() -> None:
    """★ 「缺省即不通过」：无法评估返回 `None`，**绝不返回 0**。

    返回 0 会让"未知"被当成"极差"或"极好"（取决于方向），造成误判。
    """
    assert cross_chapter_overlap("abc", []) is None, "无 prev 必须 None"
    assert dialogue_ratio("") is None
    assert intra_redundancy("") is None
    assert intra_redundancy("短") is None
    assert analyze("").cross_chapter_overlap is None


def test_cliche_list_is_complete() -> None:
    """套话表必须包含旧项目全部条目（逐条抄录）。"""
    from storyforge.scorecard.literal import CLICHES

    for required in ("仿佛", "瞬间", "嘴角勾起", "命运的齿轮", "综上所述"):
        assert required in CLICHES, f"套话表缺 {required}"
    assert len(CLICHES) >= 45


# --------------------------------------------------------------------------- #
# 双层阈值（P2 的关键设计）
# --------------------------------------------------------------------------- #


def test_hard_thresholds_do_not_flag_everything() -> None:
    """★ hard 层必须**只抓明确缺陷** —— 不能每章都触发。

    初版单层阈值（`dialogue_ratio_min=0.25`）实测 **47/47 章全部触发**
    （书 23 的 dialogue p95 才 0.292）→ 闸门退化成"全部送审"，
    等于没有闸门，且 LLM 审校成本爆炸。

    双层设计后：hard 层触发 ~10 章，加黄金三章共 13/47 = 28%。
    这是从真实分布标定出来的可用水位。
    """
    texts = _load_book23()
    cards = [
        evaluate(t, global_no=i + 1, prev_texts=texts[max(0, i - 5) : i])
        for i, t in enumerate(texts)
    ]
    need_review = sum(1 for c in cards if c.needs_review)
    ratio = need_review / len(cards)
    assert ratio < 0.5, (
        f"闸门筛不掉足够章节（{ratio:.0%} 需审校）—— "
        f"阈值过严，闸门等于没有。需按真实分布重新标定。"
    )
    assert need_review > 0, "全不触发说明阈值过松"


def test_forced_review_covers_golden_three() -> None:
    """黄金三章必须无条件送审（D-14 C-5）。"""
    th = Thresholds()
    for ch in (1, 2, 3):
        card = evaluate("正文" * 3000, global_no=ch)
        assert card.forced_review, f"第{ch}章必须强制审校"
    card4 = evaluate("正文" * 3000, global_no=99)
    assert not card4.forced_review


def test_climax_and_volume_end_are_forced() -> None:
    """高潮章与卷末章必须无条件送审。"""
    assert evaluate("正文" * 3000, global_no=50, beat_type="climax").forced_review
    assert evaluate("正文" * 3000, global_no=50, beat_type="boss").forced_review
    assert evaluate("正文" * 3000, global_no=50, is_volume_end=True).forced_review


def test_score_is_deterministic() -> None:
    """★ 打分必须可复现（同输入同输出）—— 这是确定性打分卡的基本要求。

    旧项目 `prose_style.py` 的模块 docstring 明确要求
    「同一输入任何时刻都得到同一结果（无随机、无时间、无阈值自适应）」。
    """
    text = "他走进房间，看见桌上的信。「你来了。」她说。" * 30
    a = evaluate(text, global_no=5)
    b = evaluate(text, global_no=5)
    assert a.score == b.score
    assert a.to_dict() == b.to_dict()


def test_scorecard_reports_failed_dimensions() -> None:
    """失败维度必须可定位（供定向重写使用）。"""
    # 无对话、高重叠 → 应命中多个维度
    text = "他走了过去。" * 400
    card = evaluate(text, global_no=5)
    assert card.needs_review
    assert card.failed_dimensions(), "必须能指出哪些维度没达标"
    names = {d.name for d in card.failed_dimensions()}
    assert "dialogue_ratio" in names


def test_low_score_trigger_present_when_below_threshold() -> None:
    from storyforge.scorecard.aggregate import TriggerReason

    text = "他走了过去。" * 400
    card = evaluate(text, global_no=5)
    assert TriggerReason.LOW_SCORE in card.triggers


def test_summary_ending_is_not_penalized() -> None:
    """★ `summary` 章末**只观测不判罚**。

    依据：旧项目实测 `summary = 0` 与 `time_skip = 0`，
    说明「不要总结式结尾」的规则本就生效，它不是硬伤。
    `02-根因与架构对策` 的「不要做」清单里明确写着
    「不要把总结式结尾当硬伤」。
    """
    from storyforge.domain.enums import EndingPattern
    from storyforge.scorecard.aggregate import TriggerReason

    # 造一个以总结句收尾的章节
    body = "他走进屋子，坐下，喝了口水，看着窗外的雨。" * 60
    text = body + "\n这一夜，他终于明白了什么。"
    card = evaluate(text, global_no=9)
    assert card.literal.ending == EndingPattern.SUMMARY
    assert TriggerReason.ENDING_OMEN not in card.triggers, (
        "summary 收尾不得触发 ENDING_OMEN（只观测不判罚）"
    )


def test_omen_ending_is_flagged() -> None:
    """贴片式假钩子（omen）必须触发 —— 旧项目「反AI禁令」明令禁止。"""
    from storyforge.domain.enums import EndingPattern
    from storyforge.scorecard.aggregate import TriggerReason

    body = "他处理完了手头的事，合上本子，站起身准备离开。" * 60
    text = body + "\n走廊深处，仿佛有什么东西在暗中窥视。"
    card = evaluate(text, global_no=9)
    assert card.literal.ending == EndingPattern.OMEN
    assert TriggerReason.ENDING_OMEN in card.triggers


# --------------------------------------------------------------------------- #
# 无依据新名词：必须过滤旧项目实测的误报
# --------------------------------------------------------------------------- #


def test_unsupported_entities_filters_common_nouns() -> None:
    """★ 必须过滤旧项目实测的误报。

    旧项目把 `石头`(8) `院落`(5) `老者`(13) `动作`(6) `油灯`(8) `书架`(14)
    等**普通名词**当成「无依据新名词」→ 噪声淹没真命中
    （`docs/TODO_20260913.md` §7.2）。
    """
    from storyforge.scorecard.structural import find_unsupported_entities

    prose = (
        "他推开石门，院落里堆着石头，老者坐在木棚下摇着蒲扇。"
        "油灯的光晕落在地面，书架上的线条清晰可见。"
        "他记录下这些，内腑隐隐作痛。" * 3
    )
    found = find_unsupported_entities(prose, known_names=set())
    for noise in (
        "石头", "院落", "木棚", "老者", "动作", "蒲扇", "油灯", "光晕",
        "石门", "线条", "地面", "书架", "光线", "内腑", "记录",
    ):
        assert noise not in found, f"普通名词「{noise}」不应被判为无依据专名（旧项目实测误报）"


def test_unsupported_entities_detects_real_proper_nouns() -> None:
    """真专名必须仍能被发现（否则过滤过头，闸门失效）。"""
    from storyforge.scorecard.structural import find_unsupported_entities

    prose = "他提起青锋剑，走向云岚宗。青锋剑在云岚宗门前震动。"
    found = find_unsupported_entities(prose, known_names=set(), min_occurrences=2)
    assert "青锋剑" in found or "云岚宗" in found, f"真专名未被发现：{found}"


def test_known_names_are_excluded() -> None:
    from storyforge.scorecard.structural import find_unsupported_entities

    prose = "青锋剑出鞘。青锋剑归鞘。"
    assert find_unsupported_entities(prose, known_names={"青锋剑"}) == []


# --------------------------------------------------------------------------- #
# 结构层
# --------------------------------------------------------------------------- #


def test_outline_adherence_returns_none_without_scenes() -> None:
    """无细纲场景 → `None`（缺省即不通过），不是 0。"""
    from storyforge.domain.outline import ChapterPlan
    from storyforge.scorecard.structural import measure_outline_adherence

    plan = ChapterPlan(global_no=1, volume_no=1)
    adherence, planned, detected = measure_outline_adherence("正文", plan)
    assert adherence is None
    assert planned == 0


def test_outline_adherence_detects_implemented_scenes() -> None:
    from storyforge.domain.outline import ChapterPlan, SceneBeat
    from storyforge.scorecard.structural import measure_outline_adherence

    plan = ChapterPlan(
        global_no=1, volume_no=1,
        scenes=[
            SceneBeat(
                seq=0,
                goal="陈砚查阅值班登记簿",
                obstacle="登记簿缺页",
                outcome="发现十月十日空白",
                why_now="需要确认上一班记录",
            )
        ],
    )
    prose = "陈砚翻开值班登记簿，仔细查阅每一页，发现十月十日那一格是空白的。"
    adherence, planned, detected = measure_outline_adherence(prose, plan)
    assert planned == 1
    assert detected == 1
    assert adherence == 1.0


def test_hook_landed_returns_none_without_hook() -> None:
    from storyforge.domain.outline import ChapterPlan
    from storyforge.scorecard.structural import measure_hook_landed

    plan = ChapterPlan(global_no=1, volume_no=1)
    assert measure_hook_landed("正文", plan) is None


def test_state_consistency_detects_missing_bridge() -> None:
    """上一章留下未收束事件、本章开头未承接 → 违规。

    旧项目实测：章间跳变是 Editor 低分主因（书 1005 第 2 章从
    「拒绝给李巡长开门」直接跳到「在镇公所与李巡长同席」）。
    """
    from storyforge.domain.outline import ChapterPlan, WorldStateSnapshot
    from storyforge.scorecard.structural import check_state_consistency

    plan = ChapterPlan(global_no=2, volume_no=1)
    prev = WorldStateSnapshot(open_thread="陈砚拒绝给李巡长开门，两人隔门对峙")
    prose = "第二天早上，陈砚在镇公所与李巡长同席吃饭，气氛融洽。"
    violations = check_state_consistency(prose, plan, prev_exit=prev)
    assert violations, "未承接上一章未收束事件必须报违规"


def test_state_consistency_ok_when_bridged() -> None:
    from storyforge.domain.outline import ChapterPlan, WorldStateSnapshot
    from storyforge.scorecard.structural import check_state_consistency

    plan = ChapterPlan(global_no=2, volume_no=1)
    prev = WorldStateSnapshot(open_thread="陈砚拒绝给李巡长开门")
    prose = "陈砚仍站在门内，听着李巡长的脚步渐渐远去。他没有开门。"
    assert check_state_consistency(prose, plan, prev_exit=prev) == []


# --------------------------------------------------------------------------- #
# 与旧项目坏样本的对照：书 23 是"要超越的基线"
# --------------------------------------------------------------------------- #


def test_book23_is_flagged_for_low_dialogue() -> None:
    """书 23 的对话占比 p50 仅 10.6%，低于任何合理目标 —— 必须被系统性识别。

    这正是用户痛点「AI 味重、描写僵硬」的量化证据：
    Editor 反复批评「对话占比极低」，而旧配置 `dialogue_floor = 0.12` 却判它合格。
    """
    texts = _load_book23()
    ratios = [dialogue_ratio(t) for t in texts]
    valid = [r for r in ratios if r is not None]
    p50 = statistics.median(valid)
    assert p50 < 0.25, (
        f"书 23 对话占比 p50 = {p50:.3f}，应低于新目标 0.25"
        f"（旧 dialogue_floor=0.12 过松的判断依据）"
    )
    # 明确记录：按新目标，绝大多数章不达标
    below = sum(1 for r in valid if r < 0.25)
    assert below / len(valid) > 0.8, "书 23 绝大多数章低于新 target 水位"
